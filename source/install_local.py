"""Reinstallable local bootstrap; refuses to replace an existing plugin."""
import argparse
from pathlib import Path
import shutil
import subprocess


def install_files(source, destination, reuse_existing=False):
    source, destination = Path(source), Path(destination)
    payload = [source / '__init__.py', source / 'plugin.yaml', *sorted((source / 'plaud_obsidian').glob('*.py'))]
    if len(payload) < 3 or not all(p.is_file() for p in payload):
        raise RuntimeError('PLUGIN_SOURCE_MISSING')
    if reuse_existing:
        if not all((destination / p.relative_to(source)).is_file()
                   and (destination / p.relative_to(source)).read_bytes() == p.read_bytes() for p in payload):
            raise RuntimeError('PLUGIN_PAYLOAD_MISMATCH')
        return len(payload)
    destination.mkdir(parents=True, exist_ok=False)
    for original in payload:
        target = destination / original.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, target)
        if original.read_bytes() != target.read_bytes():
            raise RuntimeError('INSTALL_READBACK_FAILED')
    return len(payload)


def main():
    from hermes_constants import get_hermes_home
    parser = argparse.ArgumentParser()
    parser.add_argument('--vault', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--runtime-only', action='store_true',
                        help='Configure runtime for an already installed matching plugin; do not replace files')
    args = parser.parse_args()
    from plaud_obsidian.engine import paths
    home = get_hermes_home()
    state = home / 'plugin-data' / 'hermes-plaud'
    destination = home / 'plugins' / 'hermes-plaud'
    source = Path(__file__).parent
    if destination.exists() and not args.runtime_only:
        raise RuntimeError('PLUGIN_ALREADY_EXISTS')
    if args.runtime_only:
        install_files(source, destination, reuse_existing=True)
    paths({'vault': args.vault, 'output': args.output, 'state': str(state)})
    node = Path(shutil.which('node') or '').resolve()
    npm_cli = node.parent / 'node_modules' / 'npm' / 'bin' / 'npm-cli.js'
    if not node.is_file() or not npm_cli.is_file():
        raise RuntimeError('NODE_AND_NPM_REQUIRED')
    runtime = state / 'runtime'
    subprocess.run([str(node), str(npm_cli), 'install', '--prefix', str(runtime), '--ignore-scripts',
                    '--no-audit', '--no-fund', '--save-exact', '@plaud-ai/mcp@0.3.14'], check=True)
    count = install_files(source, destination, reuse_existing=args.runtime_only)
    from hermes_cli.plugins import PluginManager
    from hermes_cli.plugins_manifest import parse_manifest_file
    manifest = parse_manifest_file(destination / 'plugin.yaml', destination, 'user', '')
    if manifest is None:
        raise RuntimeError('PLUGIN_MANIFEST_INVALID')
    manager = PluginManager()
    manager._load_plugin(manifest)
    result = manager._plugin_commands['plaud']['handler'](
        'configure --vault "' + args.vault + '" --output "' + args.output + '"')
    import json
    if json.loads(result).get('configured') is not True:
        raise RuntimeError('PLUGIN_CONFIGURE_FAILED')
    print('Installed files:', count)
    print(manager._plugin_commands['plaud']['handler']('status'))


if __name__ == '__main__':
    main()
