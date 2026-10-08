"""Plugin entry contract without PLAUD credentials or inference."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch


class Context:
    def __init__(self):
        self.values = {}
        self.commands = {}
        self.llm = None
    def get_config(self, key, default=None):
        return self.values.get(key, default)
    def set_config(self, key, value):
        self.values[key] = value
    def register_command(self, name, handler, **kwargs):
        self.commands[name] = handler
    def register_cli_command(self, name, help, setup_fn, handler_fn, **kwargs):
        self.cli = (name, setup_fn, handler_fn)


class PluginTests(unittest.TestCase):
    def test_register_configure_status_and_bad_command(self):
        entry = Path(__file__).resolve().parents[1] / '__init__.py'
        self.assertTrue(entry.is_file(), 'Plugin entry missing')
        spec = importlib.util.spec_from_file_location('plaud_entry_test', entry)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        ctx = Context()
        module.register(ctx)
        self.assertIn('plaud', ctx.commands)
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as home, patch.dict(
                'sys.modules', {'hermes_constants': SimpleNamespace(get_hermes_home=lambda: Path(home))}):
            output = ctx.commands['plaud']('configure --vault "' + folder + '" --output "Meeting Minutes"')
            self.assertIn('configured', output)
            self.assertEqual(Path(ctx.values['vault']), Path(folder).resolve())
            self.assertEqual(ctx.values['output'], 'Meeting Minutes')
            self.assertIn('Meeting Minutes', ctx.commands['plaud']('status'))
            structured = lambda **kwargs: None
            ctx.llm = SimpleNamespace(complete_structured=structured)
            module = ctx.commands['plaud'].__globals__
            with patch.dict(module, {'NativePlaud': lambda settings: SyntheticSource(),
                                     'Translator': lambda complete, state: self.assertIs(complete, structured),
                                     'sync': lambda *args: {'records_scanned': 0}}):
                self.assertIn('records_scanned', ctx.commands['plaud']('sync'))
        self.assertIn('INVALID_COMMAND', ctx.commands['plaud']('delete'))


class SyntheticSource:
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass


if __name__ == '__main__':
    unittest.main()
