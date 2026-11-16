"""Manual all-record import; only new, changed or missing output is written."""
from contextlib import contextmanager, ExitStack
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any
from .documents import label, render_appendix, render_transcript
from .mcp_transport import MCPToolError, RECORD_ID_PATTERN
from .media import (summary_card_ids, store_summary_image, embed_summary_images,
                    summary_image_receipt, read_summary_image_receipt)

FORMAT_VERSION = 1


class TranslationUnavailable(RuntimeError):
    def __init__(self, rows):
        super().__init__('TRANSLATION_TRANSPORT_UNAVAILABLE')
        self.rows = rows


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


def error_diagnostic(exc) -> dict[str, Any]:
    """Only known boundary diagnostics or exact local codes; never arbitrary attributes."""
    if isinstance(exc, MCPToolError):
        return exc.diagnostic
    if isinstance(exc, TranslationUnavailable):
        return dict(code='TRANSLATION_TRANSPORT_UNAVAILABLE', tool=None, retryable=True,
                    message='번역 모델 연결 복구에 실패했습니다. 원문 저장과 번역 완료 여부를 확인하세요.')
    if (type(exc), str(exc)) in ((ValueError, 'PLAUD_ID_INVALID'), (RuntimeError, 'INVALID_RECORD_ID')):
        return dict(code=str(exc), tool=None, retryable=False,
                    message='Invalid PLAUD record ID: expected 32 hexadecimal characters, optionally prefixed with f_ or of_. IDs are not normalized.')
    return {}


def safe_error(exc):
    diagnostic = error_diagnostic(exc)
    if diagnostic:
        return diagnostic['code'] + ': ' + diagnostic['message']
    code = str(exc)
    return code if re.fullmatch(r"[A-Z][A-Z0-9_]{2,100}", code) else type(exc).__name__


def record_id(value):
    if not isinstance(value, str) or not re.fullmatch(RECORD_ID_PATTERN, value):
        raise RuntimeError("INVALID_RECORD_ID")
    return value


def readable_target(output, record, key, existing=None, reserved=()):
    """Use source wall-clock time, not a guessed timezone conversion."""
    record_id(record['id'])
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", key):
        raise RuntimeError("INVALID_ARTIFACT_KEY")
    parent = existing.parent if existing is not None else output
    value = record.get('start_at') or record.get('created_at')
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            date = datetime.fromtimestamp(value / 1000 if value > 100000000000 else value, timezone.utc)
        else:
            date = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        prefix = date.strftime('%Y-%m-%d %H-%M-%S')
        if existing is None:
            parent = output / f'{date.year:04d}' / f'{date.month:02d}'
    except (ValueError, TypeError, OverflowError, OSError):
        prefix = '날짜 미상'
        if existing is None:
            parent = output / prefix
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f\[\]#^]', ' ', record.get('title') or '제목 없음')
    title = re.sub(r'\s+', ' ', title).strip(' .') or '제목 없음'
    suffix = ' - ' + key.split('-')[0]
    # ponytail: cap path/UTF-8 component length; full title stays in the note.
    budget = min(200, 240 - len(str(parent.resolve())))
    limit = budget - len((prefix + ' - ' + suffix + '.md').encode('utf-8')) - 11
    if limit < 3:
        raise RuntimeError('OUTPUT_PATH_TOO_LONG')
    title = title.encode('utf-8')[:limit].decode('utf-8', 'ignore').rstrip(' .')
    name = prefix + ' - ' + title + suffix
    target = parent / (name + '.md')
    if target != existing and (target.exists() or target.is_symlink() or target in reserved):
        token = digest([record['id'], key])[:8]
        target = parent / (name + ' (' + token + ').md')
        if target != existing and (target.exists() or target.is_symlink() or target in reserved):
            raise RuntimeError('UNRELATED_DESTINATION_EXISTS')
    if target.is_symlink() or not target.resolve().is_relative_to(output.resolve()):
        raise RuntimeError('UNSAFE_OUTPUT_PATH')
    return target


def rename_note(old, new, expected):
    if old == new:
        return
    if old.is_symlink() or old.read_bytes() != expected:
        raise RuntimeError('DESTINATION_CHANGED_DURING_IMPORT')
    if new.exists() or new.is_symlink():
        raise RuntimeError('UNRELATED_DESTINATION_EXISTS')
    if os.name == 'nt':
        os.rename(old, new)  # Windows rename never replaces an existing destination.
    else:
        os.link(old, new)  # Atomic no-clobber; both paths are on the vault filesystem.
        old.unlink()
    if new.read_bytes() != expected:
        raise RuntimeError('OUTPUT_READBACK_FAILED')


def target_path(output, rid, key, existing_index=None, record=None):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", key):
        raise RuntimeError("INVALID_ARTIFACT_KEY")
    matches = (existing_index or {}).get((rid, key), [])
    if len(matches) > 1:
        raise RuntimeError("MULTIPLE_EXISTING_TARGETS")
    if matches:
        target = matches[0]
    else:
        target = readable_target(output, record, key) if record is not None else output / f"{rid}-{key}.md"
    if target.is_symlink() or not target.resolve().is_relative_to(output.resolve()):
        raise RuntimeError("UNSAFE_OUTPUT_PATH")
    return target


def existing_targets(output):
    index = {}
    pattern = re.compile(r"(?:\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}-)?("
                         + RECORD_ID_PATTERN + r")-([A-Za-z0-9_-]{1,160})\.md")
    for path in output.rglob("*.md"):
        match = pattern.fullmatch(path.name)
        if match:
            index.setdefault(match.groups(), []).append(path)
        elif not path.is_symlink() and path.resolve().is_relative_to(output.resolve()):
            meta, _ = read_note(path.read_bytes())
            rid, key = meta.get('plaud_record_id'), meta.get('plaud_artifact_key')
            if (meta.get('plaud_plugin') == 'hermes-plaud' and isinstance(rid, str)
                    and re.fullmatch(RECORD_ID_PATTERN, rid) and isinstance(key, str)
                    and re.fullmatch(r'[A-Za-z0-9_-]{1,160}', key)):
                index.setdefault((rid, key), []).append(path)
    return index


def make_note(record, artifact, source_hash, body, translation_pending=False, *, media=None, media_pending=False):
    meta = {"plaud_plugin": "hermes-plaud", "plaud_record_id": record["id"],
            "plaud_artifact_key": artifact["key"], "plaud_kind": artifact["kind"],
            "plaud_source_sha256": source_hash, "plaud_render_sha256": digest(body.encode("utf-8")),
            "plaud_format_version": FORMAT_VERSION,
            "plaud_translation": "incomplete-transport" if translation_pending else "full-translation-unreviewed",
            "title": record.get("title", ""),
            "recorded_at": record.get("start_at") or record.get("created_at", "")}
    if media is not None:
        meta['plaud_media'] = media
        meta['plaud_media_status'] = 'incomplete' if media_pending else 'complete'
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
    units = artifact['units'] if artifact['kind'] == 'Transcript' else [{'id': '0', 'text': artifact['text']}]
    return render_rows(record, artifact, translator.translate(units))


def render_rows(record, artifact, rows):
    if artifact["kind"] == "Transcript":
        content = render_transcript(artifact["units"], rows)
    else:
        content = render_appendix(artifact["text"], rows)
    return (f"# {label(record.get('title') or record['id'])} — {artifact['kind']}\n\n"
            f"**녹음 시각:** {label(record.get('start_at') or record.get('created_at', '알 수 없음'))}\n\n"
            + content)


def apply_summary_media(settings, record, data, image_source, report):
    """Decorate a text-only render; ownership receipts never come from note frontmatter."""
    meta, body = read_note(data)
    cards = summary_card_ids(body)
    if not cards:
        return data
    expected_cards = set(cards) | set(record.get('summary_card_ids', []))
    for artifact in record.get('artifacts', []):
        if artifact['kind'] == 'Summary':
            expected_cards.update(summary_card_ids(artifact['text']))
    output, state = paths(settings)
    cache = state / 'media-cache' / (record_id(record['id']) + '.json')
    if any(path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())
           for path in (cache.parent, cache)):
        raise RuntimeError('SUMMARY_IMAGE_CACHE_INVALID')
    try:
        saved = json.loads(cache.read_text(encoding='utf-8')) if cache.exists() else {}
    except (ValueError, OSError):
        raise RuntimeError('SUMMARY_IMAGE_CACHE_INVALID') from None
    if not isinstance(saved, dict):
        raise RuntimeError('SUMMARY_IMAGE_CACHE_INVALID')
    vault = Path(settings['vault']).resolve()
    pending = saved.get('_pending', {})
    if not isinstance(pending, dict):
        raise RuntimeError('SUMMARY_IMAGE_CACHE_INVALID')
    for card, planned in pending.items():
        try:
            published = read_summary_image_receipt(vault, output, record, card, planned)
        except RuntimeError as exc:
            current = saved.get(card)
            if str(exc) != 'SUMMARY_IMAGE_OWNERSHIP_MISMATCH' or current is None:
                raise
            if read_summary_image_receipt(vault, output, record, card, current) is None:
                raise RuntimeError('SUMMARY_IMAGE_OWNERSHIP_MISMATCH') from None
        else:
            if published is not None:
                saved[card] = planned
    if pending:
        saved.pop('_pending')
        save_json(cache, saved)
    receipts = []
    for card in cards:
        # Re-fetch on each manual run so a changed cover cannot hide behind unchanged text.
        image = image_source.read_summary_image(record['id'], card,
                                               expected_card_ids=sorted(expected_cards))
        if not isinstance(image, dict) or not isinstance(image.get('data'), bytes):
            raise RuntimeError('SUMMARY_IMAGE_RESPONSE_INVALID')
        previous = saved.get(card)
        planned = summary_image_receipt(vault, output, record, card, image['data'], image.get('content_type'))
        if (vault / planned['path']).exists():
            if previous is None:
                raise RuntimeError('UNRELATED_DESTINATION_EXISTS')
            if previous.get('path') != planned['path']:
                raise RuntimeError('SUMMARY_IMAGE_OWNERSHIP_MISMATCH')
            read_summary_image_receipt(vault, output, record, card, previous)
        # A private durable intent precedes publication, including first creation and replacement.
        saved['_pending'] = {card: planned}
        save_json(cache, saved)
        try:
            receipt = store_summary_image(vault, output, record, card,
                                          image['data'], image.get('content_type'), previous=previous)
        except RuntimeError as exc:
            if str(exc) in ('UNRELATED_DESTINATION_EXISTS', 'SUMMARY_IMAGE_OWNERSHIP_MISMATCH'):
                saved.pop('_pending')
                save_json(cache, saved)
            raise
        stats = report.setdefault('media', {'downloaded': 0, 'reused': 0, 'failed': 0})
        stats['downloaded'] += 1
        stats['reused'] += int(receipt == previous)
        saved[card] = receipt
        saved.pop('_pending')
        save_json(cache, saved)
        receipts.append(receipt)
    body = embed_summary_images(body, receipts)
    artifact = {'key': meta['plaud_artifact_key'], 'kind': meta['plaud_kind']}
    return make_note(record, artifact, meta['plaud_source_sha256'], body,
                     translation_pending=meta.get('plaud_translation') == 'incomplete-transport', media=receipts)


def media_failure(report, rid, exc):
    allowed = {'INVALID_SUMMARY_CARD_REF', 'INVALID_SUMMARY_CARD_ID', 'INVALID_SUMMARY_IMAGE',
               'UNSAFE_SUMMARY_IMAGE_PATH', 'SUMMARY_IMAGE_OWNERSHIP_MISMATCH',
               'UNRELATED_DESTINATION_EXISTS', 'SUMMARY_IMAGE_CACHE_INVALID',
               'SUMMARY_IMAGE_RESPONSE_INVALID', 'PLAUD_IMAGE_DOWNLOAD_FAILED',
               'IMAGE_WEB_LOGIN_REQUIRED', 'IMAGE_WEB_BROWSER_UNAVAILABLE',
               'IMAGE_WEB_BROWSER_BUSY', 'IMAGE_WEB_BROWSER_OWNERSHIP',
               'IMAGE_WEB_TIMEOUT', 'IMAGE_WEB_PROTOCOL', 'IMAGE_WEB_RESPONSE_INVALID',
               'IMAGE_WEB_CARD_MISSING', 'IMAGE_WEB_CARD_AMBIGUOUS', 'IMAGE_WEB_IMAGE_IDENTITY',
               'IMAGE_WEB_PROFILE_INVALID', 'IMAGE_WEB_FAILED', 'IMAGE_URL_UNSAFE',
               'IMAGE_WEB_AUTH_INVALID', 'IMAGE_WEB_AUTH_BUSY', 'IMAGE_WEB_AUTH_IDENTITY',
               'IMAGE_WEB_API_FAILED', 'IMAGE_WEB_CRYPTO_UNAVAILABLE',
               'IMAGE_DOWNLOAD_FAILED', 'IMAGE_DOWNLOAD_INVALID'}
    code = str(exc) if str(exc) in allowed else 'SUMMARY_IMAGE_FAILED'
    report.setdefault('media', {'downloaded': 0, 'reused': 0, 'failed': 0})['failed'] += 1
    diagnostic = {'code': code, 'tool': None,
                  'message': '표지 이미지 수집에 실패했습니다. 기존 원문·번역은 보존하며 다음 실행에서 다시 확인합니다.'}
    if code in ('IMAGE_WEB_LOGIN_REQUIRED', 'IMAGE_WEB_AUTH_INVALID'):
        diagnostic.update(auth_required=True, retryable=False,
                          message='표지 API 인증을 연결해야 합니다. hermes plaud image-login으로 외부 Aside 로그인 후 새 인증을 발급받으세요. 평상시 이미지 수집은 브라우저 없이 처리하며 원문·번역은 보존했습니다.')
    elif code == 'IMAGE_WEB_AUTH_IDENTITY':
        diagnostic.update(auth_required=True, retryable=False,
                          message='PLAUD Web과 공식 MCP의 계정·workspace가 일치하지 않습니다. 외부 Aside에서 같은 계정으로 로그인한 후 image-login을 실행하세요.')
    elif code == 'IMAGE_WEB_AUTH_BUSY':
        diagnostic.update(retryable=True, message='다른 이미지 인증 작업이 진행 중입니다. 완료 후 다시 실행하세요. 기존 데이터는 보존했습니다.')
    report['failed'].append({'record_id': rid, 'error': code, 'diagnostic': diagnostic})


def sync_existing_media(settings, *, media_source=None):
    """Backfill existing owned Summary images without source import or any model call."""
    output, state = paths(settings)
    report = {'notes_scanned': 0, 'notes_updated': 0, 'unchanged': 0, 'failed': [], 'targets': []}
    backup = None
    manifest = []
    with writer_lock(state), ExitStack() as resources:
        indexed = existing_targets(output)
        record_cards = {}
        uncertain_cards = set()
        for (rid, key), targets in indexed.items():
            if not key.startswith('Summary-'):
                continue
            if len(targets) != 1:
                uncertain_cards.add(rid)
                continue
            try:
                meta, body = read_note(targets[0].read_bytes())
                if ((meta.get('plaud_plugin'), meta.get('plaud_record_id'), meta.get('plaud_artifact_key'))
                        == ('hermes-plaud', rid, key) and meta.get('plaud_kind') == 'Summary'):
                    record_cards.setdefault(rid, set()).update(summary_card_ids(body))
            except (OSError, ValueError):
                uncertain_cards.add(rid)
        for (rid, key), targets in sorted(indexed.items()):
            if not key.startswith('Summary-'):
                continue
            try:
                if len(targets) != 1:
                    raise RuntimeError('MULTIPLE_EXISTING_TARGETS')
                target = targets[0]
                existing = target.read_bytes()
                meta, body = read_note(existing)
                if (meta.get('plaud_plugin'), meta.get('plaud_record_id'), meta.get('plaud_artifact_key')) != ('hermes-plaud', rid, key):
                    raise RuntimeError('UNRELATED_DESTINATION_EXISTS')
                if meta.get('plaud_kind') != 'Summary' or not summary_card_ids(body):
                    continue
                report['notes_scanned'] += 1
                if meta.get('plaud_render_sha256') != digest(body.encode('utf-8')):
                    raise RuntimeError('ARTIFACT_INTEGRITY_FAILED')
                if rid in uncertain_cards:
                    raise RuntimeError('IMAGE_WEB_CARD_AMBIGUOUS')
                if media_source is None:
                    from .web_media import WebMedia
                    media_source = resources.enter_context(WebMedia(settings))
                record = {'id': rid, 'title': meta.get('title', ''), 'start_at': meta.get('recorded_at'),
                          'summary_card_ids': sorted(record_cards.get(rid, set()))}
                data = apply_summary_media(settings, record, existing, media_source, report)
                if data == existing:
                    report['unchanged'] += 1
                    continue
                if target.is_symlink() or not target.resolve().is_relative_to(output.resolve()):
                    raise RuntimeError('UNSAFE_OUTPUT_PATH')
                if target.read_bytes() != existing:
                    raise RuntimeError('DESTINATION_CHANGED_DURING_IMPORT')
                if backup is None:
                    root = state / 'backups'
                    root.mkdir(exist_ok=True)
                    backup = Path(tempfile.mkdtemp(prefix='media-', dir=root))
                copy = backup / (digest([rid, key]) + '.md')
                atomic_write(copy, existing)
                manifest.append({'target': str(target), 'backup': copy.name, 'sha256': digest(existing)})
                save_json(backup / 'manifest.json', manifest)
                atomic_write(target, data)
                report['notes_updated'] += 1
                report['targets'].append(str(target))
            except Exception as exc:
                media_failure(report, rid, exc)
        if backup is not None:
            report['backup'] = str(backup)
        save_json(state / 'last-media-report.json', report)
    return report


def sync(settings, source, translator, progress=None, *, media_source=None):
    output, state = paths(settings)
    report: dict[str, Any] = dict(records_scanned=0, artifacts_seen=0, created=0, updated=0,
                  unchanged=0, restored=0, renamed=0, pending=[], failed=[], targets=[])
    with writer_lock(state), ExitStack() as resources:
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
                    target = target_path(output, rid, artifact["key"], target_index, record)
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
                    named = readable_target(output, record, artifact['key'], target)
                    same = (meta.get("plaud_source_sha256") == source_hash
                            and meta.get('plaud_translation') != 'incomplete-transport'
                            and meta.get("plaud_render_sha256") == digest(body.encode("utf-8")))
                    if same:
                        data = existing
                    else:
                        cache = state / "render-cache" / (source_hash + ".md")
                        if cache.exists():
                            data = cache.read_bytes()
                            saved_meta, saved_body = read_note(data)
                            if (saved_meta.get("plaud_source_sha256") != source_hash or
                                    saved_meta.get("plaud_render_sha256") != digest(saved_body.encode("utf-8"))):
                                raise RuntimeError("CACHE_INTEGRITY_FAILED")
                        else:
                            translation_pending = False
                            try:
                                body = render(record, artifact, translator)
                            except TranslationUnavailable as exc:
                                translation_pending = True
                                body = render_rows(record, artifact, exc.rows)
                                body += '\n\n> [!warning] 번역 미완료\n> 원문은 보존했습니다. 다음 실행에서 미완료 번역을 다시 시도합니다.\n'
                                report['failed'].append(dict(record_id=rid, error=safe_error(exc),
                                                             diagnostic=error_diagnostic(exc)))
                            data = make_note(record, artifact, source_hash, body, translation_pending=translation_pending)
                            if not translation_pending:
                                atomic_write(cache, data)
                    if artifact['kind'] == 'Summary':
                        try:
                            if summary_card_ids(artifact['text']):
                                if media_source is None:
                                    from .web_media import WebMedia
                                    media_source = resources.enter_context(WebMedia(settings))
                                data = apply_summary_media(settings, record, data, media_source, report)
                        except Exception as exc:
                            media_failure(report, rid, exc)
                            if not same:
                                saved_meta, saved_body = read_note(data)
                                data = make_note(record, artifact, source_hash, saved_body,
                                                 translation_pending=saved_meta.get('plaud_translation') == 'incomplete-transport',
                                                 media=[], media_pending=True)
                    if same and data == existing:
                        if named != target:
                            rename_note(target, named, existing)
                            target_index[(rid, artifact['key'])] = [named]
                            report['renamed'] += 1
                            report['targets'].append(str(named))
                        report["unchanged"] += 1
                        continue

                    output.mkdir(parents=True, exist_ok=True)
                    if (target.is_symlink() or named.is_symlink()
                            or not target.resolve().is_relative_to(output.resolve())
                            or not named.resolve().is_relative_to(output.resolve())):
                        raise RuntimeError('UNSAFE_OUTPUT_PATH')
                    if named != target and named.exists():
                        raise RuntimeError('DESTINATION_CHANGED_DURING_IMPORT')
                    if (target.read_bytes() if target.exists() else None) != existing:
                        raise RuntimeError("DESTINATION_CHANGED_DURING_IMPORT")
                    atomic_write(target, data)
                    if named != target:
                        rename_note(target, named, data)
                        report['renamed'] += 1
                        target = named
                    target_index[(rid, artifact['key'])] = [target]
                    report["created" if existing is None else "updated"] += 1
                    report["targets"].append(str(target))
            except Exception as exc:
                failure = {"record_id": rid, "error": safe_error(exc)}
                diagnostic = error_diagnostic(exc)
                if diagnostic:
                    failure['diagnostic'] = diagnostic
                report["failed"].append(failure)
            report["records_scanned"] += 1
            save_json(state / "last-report.json", report)
            if progress:
                progress(report)
        save_json(state / "last-report.json", report)
    return report
