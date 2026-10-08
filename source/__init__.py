"""Manual PLAUD import plugin. No hooks and no scheduler."""
import argparse
import json
from pathlib import Path
import shlex
import shutil
import sys

# Hermes imports directory plugins under an isolated namespace.
if str(Path(__file__).parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).parent))
from plaud_obsidian.engine import paths, safe_error, sync
from plaud_obsidian.provider import NativePlaud
from plaud_obsidian.translation import Translator


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError('INVALID_COMMAND')


def parser(existing=None):
    p = existing or Parser(prog='plaud', add_help=False)
    commands = p.add_subparsers(dest='action', required=True)
    configure = commands.add_parser('configure', add_help=False)
    configure.add_argument('--vault', required=True)
    configure.add_argument('--output', required=True)
    for name in ('status', 'login', 'sync'):
        commands.add_parser(name, add_help=False)
    return p


def settings(ctx):
    from hermes_constants import get_hermes_home
    state = get_hermes_home() / 'plugin-data' / 'hermes-plaud'
    return {'vault': ctx.get_config('vault', ''), 'output': ctx.get_config('output', ''),
            'state': str(state), 'runtime': str(state / 'runtime'),
            'node': str(Path(shutil.which('node') or 'missing-node').resolve())}


def execute(ctx, args):
    try:
        if args.action == 'configure':
            vault = Path(args.vault).resolve()
            # Validate before persisting either setting. Runtime state need not exist yet.
            paths(dict(settings(ctx), vault=str(vault), output=args.output))
            ctx.set_config('vault', str(vault))
            ctx.set_config('output', args.output)
            return json.dumps({'configured': True, 'vault': str(vault), 'output': args.output}, ensure_ascii=False)
        config = settings(ctx)
        if args.action == 'status':
            return json.dumps({'vault': config['vault'], 'output': config['output'],
                               'manual_only': True, 'runtime_installed': (Path(config['runtime']) /
                               'node_modules/@plaud-ai/mcp/package.json').is_file()}, ensure_ascii=False)
        paths(config)
        with NativePlaud(config) as source:
            if args.action == 'login':
                return json.dumps(source.login())
            translator = Translator(ctx.llm.complete_structured, config['state'])
            report = sync(config, source, translator)
        return json.dumps(report, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({'success': False, 'error': safe_error(exc)})


def register(ctx):
    def command(raw_args):
        try:
            # posix=False preserves Windows backslashes; strip surrounding quotes only.
            tokens = [t[1:-1] if len(t) > 1 and t[0] == t[-1] and t[0] in '\"\'' else t
                      for t in shlex.split(raw_args, posix=False)]
            return execute(ctx, parser().parse_args(tokens))
        except Exception as exc:
            return json.dumps({'success': False, 'error': safe_error(exc)})
    ctx.register_command('plaud', command, description='수동 PLAUD 원문·전체 한국어 번역 Import',
                         args_hint='configure|status|login|sync', argument_mode='text')
    ctx.register_cli_command('plaud', 'Manual PLAUD to Obsidian import', parser,
                             lambda args: print(execute(ctx, args)))
