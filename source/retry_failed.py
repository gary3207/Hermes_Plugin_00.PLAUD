"""Manual partial retry from an explicit report; prints no meeting content."""
import argparse
import json
from pathlib import Path
import shutil

from hermes_cli.plugins import PluginContext, PluginManager
from hermes_cli.plugins_manifest import parse_manifest_file
from plaud_obsidian.engine import digest, existing_targets, paths, read_note, save_json, sync, target_path
from plaud_obsidian.provider import NativePlaud
from plaud_obsidian.translation import Translator


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', required=True)
    parser.add_argument('--report', required=True)
    parser.add_argument('--vault', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    home = Path(args.home).resolve()
    state = home / 'plugin-data/hermes-plaud'
    installed = home / 'plugins/hermes-plaud'
    report = json.loads(Path(args.report).read_text(encoding='utf-8'))
    ids = list(dict.fromkeys(row['record_id'] for row in report['failed']))
    manifest = parse_manifest_file(installed / 'plugin.yaml', installed, 'user', '')
    if manifest is None:
        raise RuntimeError('PLUGIN_MANIFEST_INVALID')
    node = shutil.which('node')
    if node is None:
        raise RuntimeError('NODE_RUNTIME_MISSING')
    context = PluginContext(manifest, PluginManager())
    settings = {'state': str(state), 'runtime': str(state / 'runtime'),
                'node': str(Path(node).resolve()),
                'vault': args.vault, 'output': args.output}
    output, _ = paths(settings)
    before = {str(p): digest(p.read_bytes()) for p in output.rglob('*.md')}
    captured = {}

    def progress(result):
        print(json.dumps({key: result[key] for key in
                         ('records_total', 'records_scanned', 'created', 'updated', 'unchanged')} |
                         {'failed': len(result['failed'])}), flush=True)

    with NativePlaud(settings) as native:
        class SelectedSource:
            def list_records(self):
                return [{'id': rid} for rid in ids]

            def read_record(self, row):
                record = native.read_record(row)
                captured[record['id']] = record
                return record

        result = sync(settings, SelectedSource(), Translator(context.llm.complete_structured, state),
                      progress=progress)
    save_json(state / 'reference-retry-report.json', result)
    index = existing_targets(output)
    verified = 0
    verification_errors = []
    for rid, record in captured.items():
        for artifact in record['artifacts']:
            target = target_path(output, rid, artifact['key'], index)
            if not target.exists():
                verification_errors.append('MISSING_ARTIFACT')
                continue
            meta, body = read_note(target.read_bytes())
            originals = [u['text'] for u in artifact['units']] if artifact['kind'] == 'Transcript' else [artifact['text']]
            if (meta.get('plaud_record_id') != rid or meta.get('plaud_artifact_key') != artifact['key']
                    or meta.get('plaud_render_sha256') != digest(body.encode('utf-8'))
                    or any(text not in body for text in originals)):
                verification_errors.append('ORIGINAL_OR_READBACK_MISMATCH')
            else:
                verified += 1
    changed_existing = sum(not Path(path).exists() or digest(Path(path).read_bytes()) != checksum
                           for path, checksum in before.items())
    notes = list(output.rglob('*.md'))
    marker_count = sum(p.read_text(encoding='utf-8').count('[원문참조]') for p in notes)
    summary = {'retry_complete': True, 'records_total': result['records_total'],
               'records_scanned': result['records_scanned'], 'created': result['created'],
               'updated': result['updated'], 'unchanged': result['unchanged'],
               'failure_codes': [row['error'] for row in result['failed']],
               'pending_count': len(result['pending']), 'verified_original_artifacts': verified,
               'verification_errors': verification_errors, 'previous_files_changed': changed_existing,
               'markdown_files': len(notes), 'reference_markers': marker_count}
    save_json(state / 'reference-retry-verification.json', summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 1 if result['failed'] or verification_errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
