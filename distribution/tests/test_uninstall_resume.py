"""Interrupted cleanup contracts; synthetic payloads only, no system entries."""
from pathlib import Path
import hashlib, json, shutil, sys, tempfile, unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from distribution import package


class UninstallResumeContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='synthetic-uninstall-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / 'source'
        files = {'runtime/python.exe': b'synthetic-not-executable',
                 'runtime/pythonw.exe': b'synthetic-not-executable',
                 'distribution/bootstrap.py': b'# synthetic\n',
                 'app/pae/config.json': b'{}',
                 'app/pae/persona/default.json': b'{}'}
        for name, value in files.items():
            target = self.source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(value)
        package.atomic_json(self.source / package.MANIFEST, {
            'format': 1, 'product': 'OpenGloss', 'platform': 'windows-x64',
            'version': '0.4.0-rc.3', 'files': {
                name: {'sha256': hashlib.sha256(value).hexdigest(), 'size': len(value)}
                for name, value in files.items()}})
        self.root = self.base / 'installed'
        package.install(self.source, self.root, engine_port=54935)
        self.sentinel = self.root / 'data/synthetic-history.txt'
        self.sentinel.write_bytes(b'synthetic user data to preserve')
        self.original_delete = shutil.rmtree

    def interrupt(self, remove_data=False, tree='versions'):
        def delete(path, *args, **kwargs):
            if Path(path) == self.root / tree:
                victim = next(p for p in Path(path).rglob('*') if p.is_file())
                victim.unlink()
                raise PermissionError('synthetic deletion interruption')
            return self.original_delete(path, *args, **kwargs)
        with patch.object(package.shutil, 'rmtree', side_effect=delete):
            with self.assertRaises(PermissionError):
                package.uninstall_files(self.root, remove_data=remove_data)

    def test_interrupted_uninstall_can_resume_and_preserves_data(self):
        self.interrupt()
        self.assertTrue(package.uninstall_files(self.root))
        self.assertFalse((self.root / 'versions').exists())
        self.assertFalse((self.root / 'installation.json').exists())
        self.assertEqual(self.sentinel.read_bytes(), b'synthetic user data to preserve')

    def test_repeated_success_is_idempotent(self):
        package.uninstall_files(self.root)
        self.assertTrue(package.uninstall_files(self.root))
        self.assertTrue(self.sentinel.is_file())

    def test_retry_cannot_change_retained_data_choice(self):
        self.interrupt()
        with self.assertRaises(ValueError):
            package.uninstall_files(self.root, remove_data=True)
        self.assertTrue(self.sentinel.is_file())

    def test_remove_data_resume_keeps_original_explicit_choice(self):
        self.interrupt(remove_data=True, tree='data')
        with self.assertRaises(ValueError):
            package.uninstall_files(self.root, remove_data=False)
        self.assertTrue(package.uninstall_files(self.root, remove_data=True))
        self.assertFalse((self.root / 'data').exists())

    def test_new_foreign_root_entry_stops_retry(self):
        self.interrupt()
        outsider = self.root / 'foreign.txt'; outsider.write_bytes(b'keep')
        with self.assertRaises(ValueError): package.uninstall_files(self.root)
        self.assertEqual(outsider.read_bytes(), b'keep')

    def test_new_foreign_payload_file_stops_retry(self):
        self.interrupt()
        outsider = self.root / 'versions/foreign.txt'; outsider.write_bytes(b'keep')
        with self.assertRaises(ValueError): package.uninstall_files(self.root)
        self.assertEqual(outsider.read_bytes(), b'keep')

    def test_changed_remaining_payload_stops_retry(self):
        self.interrupt()
        remaining = next(p for p in (self.root / 'versions').rglob('*') if p.is_file())
        remaining.write_bytes(b'foreign replacement')
        with self.assertRaises(ValueError): package.uninstall_files(self.root)
        self.assertEqual(remaining.read_bytes(), b'foreign replacement')

    def test_log_alone_cannot_authorize_initial_deletion(self):
        (self.root / 'installation.json').unlink()
        package.atomic_json(self.root / 'uninstall-in-progress.json', {
            'product': 'OpenGloss', 'instance_id': 'a' * 32, 'remove_data': False})
        with self.assertRaises(ValueError): package.uninstall_files(self.root)
        self.assertTrue(self.sentinel.is_file())

    def test_final_marker_delete_failure_can_resume(self):
        unlink = Path.unlink
        def fail_marker(path, *args, **kwargs):
            if path == self.root / 'installation.json':
                raise PermissionError('synthetic final marker failure')
            return unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', new=fail_marker):
            with self.assertRaises(PermissionError): package.uninstall_files(self.root)
        self.assertTrue(package.uninstall_files(self.root))
        self.assertTrue(self.sentinel.is_file())

    def test_final_log_write_failure_after_marker_delete_can_resume(self):
        atomic = package.atomic_json
        def fail_completion(path, value):
            if (Path(path).name == 'uninstall-in-progress.json' and
                    value.get('phase') == 'complete'):
                raise PermissionError('synthetic final journal write failure')
            return atomic(path, value)
        with patch.object(package, 'atomic_json', side_effect=fail_completion):
            with self.assertRaises(PermissionError): package.uninstall_files(self.root)
        self.assertTrue(package.uninstall_files(self.root))

    def test_uninstall_log_from_different_instance_is_refused(self):
        self.interrupt()
        journal = self.root / 'uninstall-in-progress.json'
        self.assertTrue(journal.is_file(), 'durable journal must precede deletion')
        state = package.read_json(journal)
        state['installation']['instance_id'] = 'b' * 32
        package.atomic_json(journal, state)
        with self.assertRaises(ValueError): package.uninstall_files(self.root)
        self.assertTrue(self.sentinel.is_file())

    def test_missing_journal_still_requires_full_payload_verification(self):
        target = self.root / 'versions/0.4.0-rc.3/runtime/python.exe'
        target.unlink()
        with self.assertRaises(ValueError): package.uninstall_files(self.root)
        self.assertTrue(self.sentinel.is_file())


if __name__ == '__main__': unittest.main()
