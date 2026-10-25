from pathlib import Path
import contextlib, ctypes, json, subprocess, sys, tempfile, types, unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from distribution import package, uninstall_helper


class EntryOwnershipContracts(unittest.TestCase):
    @contextlib.contextmanager
    def inspect_shortcut(self, base, target, verify,quiet=False):
        root = base / 'installed'; root.mkdir(exist_ok=True)
        shortcut = base / ('OpenGloss-' + 'a' * 32 + '.lnk')
        shortcut.write_bytes(b'synthetic owned shortcut')
        arguments = '-X utf8 -B -m distribution.bootstrap run --root "' + str(root) + '"'
        if quiet:arguments+=' --no-browser'
        details = {'target': str(target), 'arguments': arguments,
                   'working_directory': str(root)}
        verifier = Mock(side_effect=verify)
        with patch.object(package, 'active_version', return_value='0.6.0'), \
             patch.object(package, 'verify_directory', verifier), \
             patch.object(uninstall_helper, '_read_run', return_value=None), \
             patch.object(uninstall_helper, '_desktop_shortcut', return_value=shortcut), \
             patch.object(uninstall_helper, '_read_shortcut', return_value=details):
            yield root, shortcut, verifier

    def test_verified_previous_version_shortcut_remains_owned_after_update(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); old = base / 'installed/versions/0.5.0'
            (old / 'runtime').mkdir(parents=True)
            target = old / 'runtime/pythonw.exe'; target.write_bytes(b'old verified runtime')
            with self.inspect_shortcut(base, target, lambda folder: {'version': '0.5.0'}) as (root, shortcut, verifier):
                snapshot = uninstall_helper.inspect_owned_entries(root, 'a' * 32)
            verifier.assert_called_once_with(old)
            self.assertEqual(snapshot['shortcut_details']['target'], str(target))
            self.assertEqual(snapshot['shortcut_sha256'],
                             __import__('hashlib').sha256(shortcut.read_bytes()).hexdigest())

    def test_shortcut_outside_installation_stays_foreign(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); external = base / 'other/runtime/pythonw.exe'
            with self.inspect_shortcut(base, external, lambda folder: self.fail('external version verified')) as (_, _, verifier):
                with self.assertRaises(ValueError):
                    uninstall_helper.inspect_owned_entries(base / 'installed', 'a' * 32)
            verifier.assert_not_called()

    def test_quiet_shortcut_remains_owned(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);old=base/'installed/versions/0.5.0';(old/'runtime').mkdir(parents=True)
            target=old/'runtime/pythonw.exe';target.write_bytes(b'verified synthetic runtime')
            with self.inspect_shortcut(base,target,lambda folder: {'version':'0.5.0'},quiet=True) as (root,_,_):
                snapshot=uninstall_helper.inspect_owned_entries(root,'a'*32)
            self.assertTrue(snapshot['shortcut_details']['arguments'].endswith('--no-browser'))

    def test_unverified_previous_version_shortcut_stays_foreign(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); old = base / 'installed/versions/0.4.0'
            (old / 'runtime').mkdir(parents=True)
            target = old / 'runtime/pythonw.exe'; target.write_bytes(b'unverified runtime')
            with self.inspect_shortcut(base, target, lambda folder: (_ for _ in ()).throw(ValueError('bad manifest'))) as (root, _, verifier):
                with self.assertRaises(ValueError):
                    uninstall_helper.inspect_owned_entries(root, 'a' * 32)
            verifier.assert_called_once_with(old)

    def run_helper(self, base, registry_values, shortcut=None, shortcut_details=None):
        root = base / 'installed'; root.mkdir()
        result = base / 'uninstall-status.json'
        instance_id = 'a' * 32
        calls = {'query': 0}
        registry = types.SimpleNamespace(
            HKEY_CURRENT_USER=1, KEY_SET_VALUE=2, REG_SZ=1,
            DeleteValue=Mock(), SetValueEx=Mock())
        class Key:
            def __enter__(self): return self
            def __exit__(self, *args): pass
        registry.OpenKey = Mock(return_value=Key())
        registry.CreateKeyEx = Mock(return_value=Key())
        def query(*args):
            index = min(calls['query'], len(registry_values) - 1)
            calls['query'] += 1
            value = registry_values[index]
            if value is None: raise FileNotFoundError
            return (value, 1)
        registry.QueryValueEx = Mock(side_effect=query)
        def folder(*args):
            args[-1].value = str(base)
            return 0
        fake_ctypes = types.SimpleNamespace(create_unicode_buffer=ctypes.create_unicode_buffer,
            windll=types.SimpleNamespace(shell32=types.SimpleNamespace(SHGetFolderPathW=folder)))
        if shortcut is not None:
            shortcut.write_bytes(b'synthetic link')
        def read_shortcut(*args, **kwargs):
            return subprocess.CompletedProcess(args, 0,
                stdout=json.dumps(shortcut_details or {}), stderr='')
        with patch.object(uninstall_helper, 'ctypes', fake_ctypes), \
             patch.object(uninstall_helper, 'winreg', registry), \
             patch.object(uninstall_helper, 'wait_for_manager', return_value=contextlib.nullcontext()), \
             patch.object(uninstall_helper, 'data_owner', return_value=contextlib.nullcontext()), \
             patch.object(package, 'installation', return_value={'instance_id': instance_id}), \
             patch.object(package, 'active_version', return_value='0.5.0'), \
             patch.object(package, 'preflight_uninstall', return_value=root, create=True), \
             patch.object(package, 'uninstall_files') as cleanup, \
             patch.object(subprocess, 'run', side_effect=read_shortcut), \
             patch.object(sys, 'argv', ['helper', str(root), instance_id, '0', str(result)]):
            uninstall_helper.main()
        return package.read_json(result), cleanup, registry

    def test_foreign_registry_command_with_root_text_is_not_removed(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); root = base / 'installed'
            foreign = 'other.exe --note --root "' + str(root) + '"'
            status, cleanup, registry = self.run_helper(base, [foreign])
            cleanup.assert_not_called()
            registry.DeleteValue.assert_not_called()
            self.assertFalse(status['complete'])
            self.assertNotEqual(status.get('program_removed'), True)

    def test_same_name_foreign_shortcut_is_not_removed(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            shortcut = base / ('OpenGloss-' + 'a' * 32 + '.lnk')
            status, cleanup, registry = self.run_helper(base, [None], shortcut,
                {'target': str(Path(temp)/'foreign-app.exe'), 'arguments': '--root "other"',
                 'working_directory': str(Path(temp)/'foreign')})
            cleanup.assert_not_called()
            self.assertTrue(shortcut.exists())
            registry.DeleteValue.assert_not_called()
            self.assertFalse(status['complete'])

    def test_entry_changed_after_program_cleanup_is_skipped_and_reported_partial(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); root = base / 'installed'
            expected = '"' + str(root / 'versions/0.5.0/runtime/pythonw.exe') + \
                '" -X utf8 -B -m distribution.bootstrap run --root "' + str(root) + '" --no-browser'
            foreign = 'other.exe --note --root "' + str(root) + '"'
            status, cleanup, registry = self.run_helper(base, [expected, foreign])
            cleanup.assert_called_once()
            registry.DeleteValue.assert_not_called()
            self.assertFalse(status['complete'])
            self.assertTrue(status.get('program_removed'))
            self.assertIn('部分', status['message'])


if __name__ == '__main__':
    unittest.main()
