"""Manual all-record import; only new, changed or missing output is written."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any
from .documents import label, render_appendix, render_transcript

FORMAT_VERSION = 1


def digest(value):
    data = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def paths(settings):
    vault = Path(settings["vault"])
    if not vault.is_absolute() or not vault.is_dir():
        raise RuntimeError("EXISTING_VAULT_REQUIRED")
    vault = vault.resolve()
    output_value = settings["output"]
    if not isinstance(output_value, str) or not output_value.strip():
        raise RuntimeError("OUTPUT_FOLDER_REQUIRED")
    output = (vault / output_value).resolve()
    state = Path(settings["state"])
    if not state.is_absolute():
        raise RuntimeError("ABSOLUTE_STATE_REQUIRED")
    state = state.resolve()
    if output == vault or not output.is_relative_to(vault):
        raise RuntimeError("OUTPUT_OUTSIDE_VAULT")
    if state.is_relative_to(vault) or vault.is_relative_to(state):
        raise RuntimeError("PRIVATE_STATE_MUST_BE_OUTSIDE_VAULT")
    return output, state


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".plaud-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if path.read_bytes() != data:
            raise RuntimeError("OUTPUT_READBACK_FAILED")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_json(path, value):
    atomic_write(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


@contextmanager
def writer_lock(state):
    state.mkdir(parents=True, exist_ok=True)
    with (state / "writer.lock").open("a+b") as stream:
        stream.seek(0)
        if stream.read(1) == b"":
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError("IMPORT_ALREADY_RUNNING") from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def safe_error(exc):
    code = str(exc)
    return code if re.fullmatch(r"[A-Z][A-Z0-9_]{2,100}", code) else type(exc).__name__


def record_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:of_)?[a-fA-F0-9]{32}", value):
        raise RuntimeError("INVALID_RECORD_ID")
    return value


def target_path(output, rid, key, existing_index=None):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", key):
        raise RuntimeError("INVALID_ARTIFACT_KEY")
    target = output / f"{rid}-{key}.md"
    matches = (existing_index or {}).get((rid, key), [])
    if len(matches) > 1:
        raise RuntimeError("MULTIPLE_EXISTING_TARGETS")
    if matches:
        target = matches[0]
    if target.is_symlink() or not target.resolve().is_relative_to(output):
        raise RuntimeError("UNSAFE_OUTPUT_PATH")
    return target


def existing_targets(output):
    index = {}
    pattern = re.compile(r"(?:\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}-)?"
                         r"((?:of_)?[a-fA-F0-9]{32})-([A-Za-z0-9_-]{1,160})\.md")
    for path in output.rglob("*.md"):
        match = pattern.fullmatch(path.name)
        if match:
            index.setdefault(match.groups(), []).append(path)
    return index


def make_note(record, artifact, source_hash, body):
    meta = {"plaud_plugin": "hermes-plaud", "plaud_record_id": record["id"],
            "plaud_artifact_key": artifact["key"], "plaud_kind": artifact["kind"],
            "plaud_source_sha256": source_hash, "plaud_render_sha256": digest(body.encode("utf-8")),
            "plaud_format_version": FORMAT_VERSION,
            "plaud_translation": "full-translation-unreviewed", "title": record.get("title", ""),
            "recorded_at": record.get("start_at") or record.get("created_at", "")}
    header = "---\n" + "\n".join(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in meta.items()) + "\n---\n\n"
    return (header + body).encode("utf-8")


def read_note(data):
    try:
        header, body = data.decode("utf-8").split("\n---\n\n", 1)
        if not header.startswith("---\n"):
            return {}, ""
        meta = {key: json.loads(value) for key, value in
                (line.split(": ", 1) for line in header[4:].splitlines())}
        return meta, body
    except (ValueError, UnicodeError):
        return {}, ""


def render(record, artifact, translator):
    if artifact["kind"] == "Transcript":
        content = render_transcript(artifact["units"], translator.translate(artifact["units"]))
    else:
        content = render_appendix(artifact["text"], translator.translate([{"id": "0", "text": artifact["text"]}]))
    return (f"# {label(record.get('title') or record['id'])} — {artifact['kind']}\n\n"
            f"**녹음 시각:** {label(record.get('start_at') or record.get('created_at', '알 수 없음'))}\n\n"
            + content)


def sync(settings, source, translator, progress=None):
    output, state = paths(settings)
    report: dict[str, Any] = dict(records_scanned=0, artifacts_seen=0, created=0, updated=0,
                  unchanged=0, restored=0, pending=[], failed=[], targets=[])
    with writer_lock(state):
        target_index = existing_targets(output)
        inventory = source.list_records()
        ids = [record_id(row["id"]) for row in inventory]
        if len(ids) != len(set(ids)):
            raise RuntimeError("DUPLICATE_RECORD_ID")
        report["records_total"] = len(ids)
        for row in inventory:
            rid = row["id"]
            try:
                record = source.read_record(row)
                if record_id(record["id"]) != rid:
                    raise RuntimeError("RECORD_ID_MISMATCH")
                keys = [a["key"] for a in record["artifacts"]]
                if len(keys) != len(set(keys)):
                    raise RuntimeError("DUPLICATE_ARTIFACT_KEY")
                for missing in record.get("pending", []):
                    report["pending"].append({"record_id": rid, "kind": missing})
                for artifact in record["artifacts"]:
                    if artifact["kind"] not in ("Transcript", "Summary", "Highlights"):
                        raise RuntimeError("UNSUPPORTED_ARTIFACT_KIND")
                    if not isinstance(artifact["text"], str) or not artifact["text"].strip():
                        raise RuntimeError("EMPTY_ARTIFACT")
                    report["artifacts_seen"] += 1
                    target = target_path(output, rid, artifact["key"], target_index)
                    source_hash = digest({"format": FORMAT_VERSION, "id": rid,
                                          "translation_policy": translator.cache_identity,
                                          "title": record.get("title", ""),
                                          "time": record.get("start_at") or record.get("created_at", ""),
                                          "artifact": artifact})
                    existing = target.read_bytes() if target.exists() else None
                    meta, body = read_note(existing) if existing else ({}, "")
                    if existing is not None and (meta.get("plaud_plugin"), meta.get("plaud_record_id"),
                                                meta.get("plaud_artifact_key")) != ("hermes-plaud", rid, artifact["key"]):
                        raise RuntimeError("UNRELATED_DESTINATION_EXISTS")
                    if (meta.get("plaud_source_sha256") == source_hash
                            and meta.get("plaud_render_sha256") == digest(body.encode("utf-8"))):
                        report["unchanged"] += 1
                        continue
                    cache = state / "render-cache" / (source_hash + ".md")
                    if cache.exists():
                        data = cache.read_bytes()
                        saved_meta, saved_body = read_note(data)
                        if (saved_meta.get("plaud_source_sha256") != source_hash or
                                saved_meta.get("plaud_render_sha256") != digest(saved_body.encode("utf-8"))):
                            raise RuntimeError("CACHE_INTEGRITY_FAILED")
                    else:
                        data = make_note(record, artifact, source_hash, render(record, artifact, translator))
                        atomic_write(cache, data)
                    output.mkdir(parents=True, exist_ok=True)
                    target = target_path(output, rid, artifact["key"], target_index)
                    if (target.read_bytes() if target.exists() else None) != existing:
                        raise RuntimeError("DESTINATION_CHANGED_DURING_IMPORT")
                    atomic_write(target, data)
                    report["created" if existing is None else "updated"] += 1
                    report["targets"].append(str(target))
            except Exception as exc:
                report["failed"].append({"record_id": rid, "error": safe_error(exc)})
            report["records_scanned"] += 1
            save_json(state / "last-report.json", report)
            if progress:
                progress(report)
        save_json(state / "last-report.json", report)
    return report
