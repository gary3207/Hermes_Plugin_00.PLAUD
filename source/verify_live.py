"""Read-only PLAUD verification plus isolated restoration of a real artifact."""
import argparse
import json
from pathlib import Path
import tempfile

from plaud_obsidian.engine import digest, paths, read_note, save_json, sync
from plaud_obsidian.provider import NativePlaud
from plaud_obsidian.translation import Translator
import shutil


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', required=True)
    parser.add_argument('--vault', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    state = Path(args.home).resolve() / 'plugin-data/hermes-plaud'
    node = shutil.which('node')
    if node is None:
        raise RuntimeError('NODE_RUNTIME_MISSING')
    config = {'state': str(state), 'runtime': str(state / 'runtime'),
              'node': str(Path(node).resolve()), 'vault': args.vault, 'output': args.output}
    output, _ = paths(config)
    before = {str(p): (digest(p.read_bytes()), p.stat().st_mtime_ns) for p in output.rglob('*.md')}
    selected_path = next(p for p in output.rglob('*.md') if read_note(p.read_bytes())[0].get('plaud_kind') == 'Transcript')
    selected_meta, _ = read_note(selected_path.read_bytes())
    selected_record = {}
    model_calls = 0

    def no_model(**kwargs):
        nonlocal model_calls
        model_calls += 1
        raise RuntimeError('MODEL_CALL_UNEXPECTED_DURING_VERIFY')

    def progress(report):
        print(json.dumps({key: report[key] for key in
                         ('records_total', 'records_scanned', 'created', 'updated', 'unchanged')} |
                         {'failed': len(report['failed'])}), flush=True)

    with NativePlaud(config) as native:
        class ReadOnlySource:
            def list_records(self):
                return native.list_records()

            def read_record(self, row):
                record = native.read_record(row)
                if record['id'] == selected_meta['plaud_record_id']:
                    selected_record.update(record)
                return record

        report = sync(config, ReadOnlySource(), Translator(no_model, state), progress=progress)
    save_json(state / 'repeat-verification-report.json', report)
    unchanged_files = all(Path(path).is_file() and
                          (digest(Path(path).read_bytes()), Path(path).stat().st_mtime_ns) == original
                          for path, original in before.items())
    assert not report['failed'], 'LIVE_VERIFY_FAILED_CHECK_REPORT'
    assert report['created'] == report['updated'] == model_calls == 0
    assert unchanged_files and report['unchanged'] == len(before)
    assert selected_record
    artifact = next(a for a in selected_record['artifacts'] if a['key'] == selected_meta['plaud_artifact_key'])
    # ponytail: replay one real artifact in a disposable private vault; never delete a user's note.
    private_checks = state / 'verification'
    private_checks.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='restore-', dir=private_checks) as folder:
        root = Path(folder)
        vault, sandbox_state = root / 'vault', root / 'state'
        vault.mkdir()
        cache = sandbox_state / 'render-cache' / (selected_meta['plaud_source_sha256'] + '.md')
        cache.parent.mkdir(parents=True)
        original = selected_path.read_bytes()
        cache.write_bytes(original)
        record = dict(selected_record, artifacts=[artifact], pending=[])

        class RecordedSource:
            def list_records(self):
                return [{'id': record['id']}]

            def read_record(self, row):
                return record

        restoration = sync({'vault': str(vault), 'output': 'Meeting Minutes', 'state': str(sandbox_state)},
                           RecordedSource(), Translator(no_model, sandbox_state))
        assert not restoration['failed'] and restoration['created'] == 1 and model_calls == 0
        restored = Path(restoration['targets'][0])
        assert restored.read_bytes() == original
    result = {'records_total': report['records_total'], 'records_scanned': report['records_scanned'],
              'unchanged': report['unchanged'], 'created': report['created'], 'updated': report['updated'],
              'failures': len(report['failed']), 'model_calls': model_calls,
              'file_hashes_and_mtimes_unchanged': unchanged_files,
              'missing_restore': 'PASS_PRIVATE_REPLAY_BYTE_IDENTICAL',
              'pending_kinds': sorted({row['kind'] for row in report['pending']})}
    save_json(state / 'repeat-verification.json', result)
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
