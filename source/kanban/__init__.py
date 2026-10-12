"""Native CLI and short dispatch observer only; no dispatcher monkeypatch."""
from pathlib import Path
from .plaud_kanban import parser, execute, dispatch_hook

def register(ctx):
    ctx.register_skill('plaud-kanban-manual-jobs',
                       Path(__file__).parent/'skills/plaud-kanban-manual-jobs/SKILL.md',
                       description='Use when operating PLAUD Kanban manual Import jobs.')
    ctx.register_cli_command('plaud-kanban', 'Manual recurring PLAUD Kanban job', parser,
                             lambda args: print(execute(args)))
    ctx.register_hook('on_kanban_dispatch_tick',dispatch_hook)
