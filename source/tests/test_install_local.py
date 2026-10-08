"""Bootstrap boundary checks without network or Hermes profile writes."""
from pathlib import Path
import tempfile
import unittest
from install_local import install_files


class InstallTests(unittest.TestCase):
    def test_existing_matching_payload_can_be_reused_without_writes(self):
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / 'plugin'
            count = install_files(source, destination)
            before = {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in destination.rglob('*') if p.is_file()}
            self.assertEqual(install_files(source, destination, reuse_existing=True), count)
            self.assertEqual(before, {str(p): (p.read_bytes(), p.stat().st_mtime_ns)
                                     for p in destination.rglob('*') if p.is_file()})
            with self.assertRaises(FileExistsError):
                install_files(source, destination)

    def test_reuse_refuses_missing_or_different_plugin(self):
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / 'plugin'
            with self.assertRaisesRegex(RuntimeError, 'PLUGIN_PAYLOAD_MISMATCH'):
                install_files(source, destination, reuse_existing=True)
            install_files(source, destination)
            entry = destination / '__init__.py'
            entry.write_bytes(b'Unrelated entry')
            with self.assertRaisesRegex(RuntimeError, 'PLUGIN_PAYLOAD_MISMATCH'):
                install_files(source, destination, reuse_existing=True)
            self.assertEqual(entry.read_bytes(), b'Unrelated entry')


if __name__ == '__main__':
    unittest.main()
