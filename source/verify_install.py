"""Exercise bootstrap/discovery in a disposable Hermes home, without account login."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', help='Optional GitHub owner/repo/source identifier')
    parser.add_argument('--revision', help='Exact remote commit for GitHub install')
    parser.add_argument('--kanban', action='store_true', help='Also install and verify the companion from the same GitHub commit')
    args = parser.parse_args()
    if args.kanban and not (args.repo and args.revision):
        parser.error('--kanban requires --repo and --revision')
    source = Path(__file__).resolve().parent
    evidence = source / 'evidence'
    evidence.mkdir(exist_ok=True)
    scratch = Path(os.environ.get('TMPDIR', str(evidence / 'scratch')))
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='plaud-install-', dir=scratch, ignore_cleanup_errors=True) as folder:
        root = Path(folder)
        home, vault = root / 'hermes', root / 'vault'
        home.mkdir()
        vault.mkdir()
        env = dict(os.environ, HERMES_HOME=str(home), PYTHONDONTWRITEBYTECODE='1')
        # Never copy auth, config or private runtime data from the real profile.
        cli = [sys.executable, '-B', '-m', 'hermes_cli.main']

        def run(command):
            result = subprocess.run(command, env=env, capture_output=True, text=True, encoding='utf-8',
                                    errors='replace', timeout=300)
            print(json.dumps({'step': Path(command[3]).name if len(command) > 3 else 'bootstrap',
                              'exit_code': result.returncode}), flush=True)
            if result.returncode:
                print(result.stdout)
                print(result.stderr, file=sys.stderr)
                raise RuntimeError('ISOLATED_INSTALL_COMMAND_FAILED')
            return result.stdout

        if args.repo:
            if not args.revision:
                raise RuntimeError('PINNED_REVISION_REQUIRED')
            run(cli + ['plugins', 'install', args.repo, '--ref', args.revision, '--no-enable', '--yes-deps'])
            bootstrap = home / 'plugins/hermes-plaud/install_local.py'
            run([sys.executable, '-B', str(bootstrap), '--runtime-only', '--vault', str(vault),
                 '--output', 'Meeting Minutes'])
        else:
            run([sys.executable, '-B', str(source / 'install_local.py'), '--vault', str(vault),
                 '--output', 'Meeting Minutes'])
        run(cli + ['plugins', 'enable', 'hermes-plaud', '--no-allow-tool-override'])
        listing = run(cli + ['plugins', 'list', '--plain', '--no-bundled'])
        assert 'hermes-plaud' in listing
        status_output = run(cli + ['plaud', 'status'])
        status = next(json.loads(line) for line in status_output.splitlines() if line.startswith('{'))
        assert status['runtime_installed'] and status['manual_only']
        assert Path(status['vault']).resolve() == vault.resolve() and status['output'] == 'Meeting Minutes'
        installed = home / 'plugins/hermes-plaud'
        payload = [source / '__init__.py', source / 'plugin.yaml', *sorted((source / 'plaud_obsidian').glob('*.py'))]
        assert all((installed / p.relative_to(source)).read_bytes() == p.read_bytes() for p in payload)
        config = {'state': str(home / 'plugin-data/hermes-plaud'),
                  'runtime': str(home / 'plugin-data/hermes-plaud/runtime')}
        import shutil

        node = shutil.which('node')
        if node is None:
            raise RuntimeError('NODE_RUNTIME_MISSING')
        config['node'] = str(Path(node).resolve())
        # Use the real MCP transport, initialization and tool discovery only; no login/account calls.
        from plaud_obsidian.provider import NativePlaud
        with NativePlaud(config):
            pass
        result = {'installation': 'PASS', 'mode': 'github-pinned' if args.repo else 'local-bootstrap',
                  'revision': args.revision, 'payload_files_equal': len(payload),
                  'fresh_cli_discovery': 'PASS', 'configuration': 'PASS',
                  'runtime_handshake': 'PASS', 'account_login_attempted': False,
                  'existing_profile_modified': False}
        if args.kanban:
            run(cli + ['plugins', 'install', args.repo.rstrip('/') + '/kanban', '--ref', args.revision,
                       '--no-enable', '--yes-deps'])
            companion = home / 'plugins/hermes-plaud-kanban'
            names = ('plugin.yaml', '__init__.py', 'plaud_kanban.py')
            assert all((companion / name).read_bytes() == (source / 'kanban' / name).read_bytes() for name in names)
            run(cli + ['plugins', 'enable', 'hermes-plaud-kanban', '--no-allow-tool-override'])
            run(cli + ['plugins', 'doctor', 'hermes-plaud-kanban', '--ci'])
            waiting = json.loads(run(cli + ['plaud-kanban', 'initialize', '--board', 'default']))['waiting']
            journal = json.loads(run(cli + ['plaud-kanban', 'status', '--board', 'default']))
            assert journal['waiting'] == waiting and not journal.get('active')
            card = json.loads(run(cli + ['kanban', '--board', 'default', 'show', waiting, '--json']))
            assert card['status'] == 'scheduled' and card['current_run_id'] is None
            assert json.loads(run(cli + ['plaud-kanban', 'tick', '--board', 'default']))['spawned'] is False
            result.update(kanban_installation='PASS', kanban_payload_files_equal=len(names),
                          scheduled_wait_no_start='PASS', kanban_account_import_attempted=False)
    destination = evidence / ('github-install.json' if args.repo else 'local-install.json')
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
