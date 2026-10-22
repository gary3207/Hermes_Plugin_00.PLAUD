"""Build a source-only delivery ZIP from an explicit file allowlist."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    source = Path(__file__).resolve().parent
    root = source.parent
    files = [root / name for name in ('README.md', 'VERIFICATION.md', 'LICENSE', 'PLAUD_Kanban_사용안내.md')]
    files += [source / 'plugin.yaml', *sorted(source.glob('*.py')),
              *sorted((source / 'plaud_obsidian').glob('*.py')),
              *sorted((source / 'tests').glob('*.py'))]
    files += [source / 'kanban' / name for name in ('plugin.yaml', '__init__.py',
              'plaud_kanban.py', 'test_plaud_kanban.py', 'test_error_details.py', 'test_presentation.py', 'fixture_controller.py', 'fixture_worker.py')]
    files += [source / 'kanban/skills/plaud-kanban-manual-jobs/SKILL.md']
    assert all(p.is_file() and not p.is_symlink() for p in files)
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    members = [p.relative_to(root).as_posix() for p in files]
    assert len(members) == len(set(members))
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path, member in zip(files, members):
            archive.writestr(member, path.read_bytes())
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None and archive.namelist() == members
        assert all(archive.read(member) == path.read_bytes() for path, member in zip(files, members))
    print(json.dumps({'archive': str(output), 'files': len(files), 'bytes': output.stat().st_size,
                      'sha256': hashlib.sha256(output.read_bytes()).hexdigest(), 'readback': 'PASS'}))


if __name__ == '__main__':
    main()
