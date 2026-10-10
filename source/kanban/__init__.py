"""Native CLI and short dispatch observer only; no dispatcher monkeypatch."""
from .plaud_kanban import parser, execute, dispatch_hook

def register(ctx):
    ctx.register_cli_command('plaud-kanban', 'Manual recurring PLAUD Kanban job', parser,
                             lambda args: print(execute(args)))
    ctx.register_hook('on_kanban_dispatch_tick',dispatch_hook)
