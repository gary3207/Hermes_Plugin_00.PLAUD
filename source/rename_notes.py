"""Rename verified plugin-owned notes, with a private backup and no model calls."""
import argparse
import json
from pathlib import Path
import time
import zipfile
from plaud_obsidian.engine import (digest, existing_targets, paths, read_note,
                                  readable_target, rename_note, save_json,
                                  target_path, writer_lock)


def migrate(settings, apply=False):
    output, state = paths(settings)
    with writer_lock(state):
        index = existing_targets(output)
        plan, reserved = [], set()
        for old in sorted(output.rglob('*.md')):
            if old.is_symlink() or not old.resolve().is_relative_to(output):
                continue
            data = old.read_bytes()
            meta, body = read_note(data)
            if meta.get('plaud_plugin') != 'hermes-plaud':
                continue
            rid, key = meta.get('plaud_record_id'), meta.get('plaud_artifact_key')
            if meta.get('plaud_render_sha256') != digest(body.encode('utf-8')):
                raise RuntimeError('MODIFIED_GENERATED_NOTE')
            if target_path(output, rid, key, index) != old:
                raise RuntimeError('MULTIPLE_EXISTING_TARGETS')
            record = {'id': rid, 'title': meta.get('title'), 'start_at': meta.get('recorded_at')}
            new = readable_target(output, record, key, old, reserved)
            reserved.add(new)
            plan.append((old, new, data, old.stat().st_mtime_ns))
        changed = [entry for entry in plan if entry[0] != entry[1]]
        backup = None
        if apply and changed:
            folder = state / 'backups'
            folder.mkdir(exist_ok=True)
            backup = folder / f'names-before-{time.time_ns()}.zip'
            with zipfile.ZipFile(backup, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
                for old, _, data, _ in changed:
                    if old.read_bytes() != data:
                        raise RuntimeError('DESTINATION_CHANGED_DURING_IMPORT')
                    archive.writestr(old.relative_to(output).as_posix(), data)
            with zipfile.ZipFile(backup) as archive:
                assert archive.testzip() is None
                assert all(archive.read(old.relative_to(output).as_posix()) == data
                           for old, _, data, _ in changed)
            for old, new, data, _ in changed:
                rename_note(old, new, data)
        if apply:
            assert all(new.read_bytes() == data and new.stat().st_mtime_ns == mtime
                       for _, new, data, mtime in plan), 'RENAME_VERIFICATION_FAILED'
            assert all(not old.exists() for old, _, _, _ in changed)
        result = {'checked': len(plan), 'renamed' if apply else 'planned': len(changed),
                  'content_changed': 0, 'model_calls': 0, 'backup': str(backup) if backup else None,
                  'paths': [{'old': str(old), 'new': str(new), 'sha256': digest(data)}
                            for old, new, data, _ in changed]}
        if apply:
            save_json(state / 'rename-report.json', result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', required=True)
    parser.add_argument('--vault', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--apply', action='store_true', help='Default is a read-only plan.')
    args = parser.parse_args()
    result = migrate({'vault': args.vault, 'output': args.output,
                      'state': str(Path(args.home).resolve() / 'plugin-data/hermes-plaud')}, args.apply)
    print(json.dumps({k: v for k, v in result.items() if k != 'paths'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
