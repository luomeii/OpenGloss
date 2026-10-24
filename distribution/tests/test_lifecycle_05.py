from pathlib import Path
import contextlib, hashlib, json, os, sqlite3, sys, tempfile, unittest, zipfile
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from distribution import bootstrap, package


class ManagementLifecycleContracts(unittest.TestCase):
    def installed_fixture(self, base):
        source = base / 'source'; source.mkdir()
        files = {'runtime/python.exe': b'synthetic runtime',
            'runtime/pythonw.exe': b'synthetic background runtime',
            'distribution/bootstrap.py': b'# synthetic\n',
            'app/pae/config.json': b'{"privacy":{"url_storage":"full","context_storage":true,"shadow_model":false}}',
            'app/pae/persona/default.json': b'{}'}
        for name, content in files.items():
            path = source / name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        manifest = {'format': 1, 'product': 'OpenGloss', 'version': '0.4.0',
            'platform': 'windows-x64', 'files': {name: {'sha256': hashlib.sha256(content).hexdigest(),
            'size': len(content)} for name, content in files.items()}}
        package.atomic_json(source / package.MANIFEST, manifest)
        root = base / 'installed'
        package.install(source, root, engine_port=54975)
        return root

    def root(self, temp):
        root = Path(temp)
        package.atomic_json(root / 'installation.json', {'product': 'OpenGloss',
            'instance_id': 'c' * 32, 'engine_port': 54975, 'manager_port': 54976})
        package.atomic_json(root / 'active.json', {'version': '0.5.0', 'previous': None})
        data = root / 'data'; data.mkdir()
        package.atomic_json(data / 'config.json', {'privacy': {'url_storage': 'full',
            'context_storage': True, 'shadow_model': False}, 'llm': {'key': 'synthetic-key'}})
        return root

    def test_privacy_updates_only_allowed_keys_and_rejects_invalid_types(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.root(temp)
            bootstrap.save_privacy(root, {'url_storage': 'host_only',
                'context_storage': False, 'shadow_model': True})
            config = package.read_json(root / 'data/config.json')
            self.assertEqual(config['privacy'], {'url_storage': 'host_only',
                'context_storage': False, 'shadow_model': True})
            self.assertEqual(config['llm']['key'], 'synthetic-key')
            for payload in ({'url_storage': 'invalid'}, {'context_storage': 1},
                            {'shadow_model': 'true'}, {'bnc_known_rank': 999}):
                with self.subTest(payload=payload), self.assertRaises(ValueError):
                    bootstrap.save_privacy(root, payload)

    def test_autostart_is_opt_in_owned_and_revocable_with_mock_registry(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.root(temp)
            registry = Mock()
            registry.read.return_value = None
            self.assertFalse(bootstrap.autostart_enabled(root, registry=registry))
            bootstrap.set_autostart(root, True, registry=registry)
            name, command = registry.write.call_args.args
            self.assertIn('c' * 32, name)
            self.assertIn('--no-browser', command)
            self.assertIn(str(root), command)
            bootstrap.set_autostart(root, False, registry=registry)
            registry.delete.assert_called_once_with(name)

    def test_export_uses_sqlite_backup_and_includes_required_data_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.root(temp); data = root / 'data'
            connection = sqlite3.connect(data / 'pae.db')
            connection.execute('create table words (word text)')
            connection.execute("insert into words values ('synthetic')")
            connection.commit(); connection.close()
            for name in ('persona.json', 'keys.json', 'http-auth.json', 'privacy-key.json'):
                (data / name).write_text('{}', encoding='utf-8')
            destination = data / 'exports/export.zip'
            package.export_data(root, destination)
            with zipfile.ZipFile(destination) as archive:
                names = set(archive.namelist())
                self.assertEqual(names, {'pae.db', 'config.json', 'persona.json',
                    'keys.json', 'http-auth.json', 'privacy-key.json'})
                self.assertNotIn('pae.db-wal', names)
                with tempfile.TemporaryDirectory() as extracted:
                    archive.extract('pae.db', extracted)
                    with contextlib.closing(sqlite3.connect(Path(extracted) / 'pae.db')) as restored:
                        self.assertEqual(restored.execute('select word from words').fetchone()[0], 'synthetic')

    def test_uninstall_rejects_unknown_root_and_links_and_preserves_data(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            unknown = base / 'unknown'; unknown.mkdir()
            (unknown / 'unrelated.txt').write_text('preserve', encoding='utf-8')
            with self.assertRaises(ValueError): package.uninstall_files(unknown)
            self.assertTrue((unknown / 'unrelated.txt').exists())
            root = self.installed_fixture(base)
            victim = root / 'versions/0.4.0/runtime/python.exe'
            original_is_symlink = Path.is_symlink
            with patch.object(Path, 'is_symlink', lambda path: path == victim or original_is_symlink(path)):
                with self.assertRaises(ValueError): package.uninstall_files(root)
            self.assertTrue(victim.exists())
            package.uninstall_files(root)
            self.assertTrue((root / 'data').is_dir())
            self.assertFalse((root / 'versions').exists())

    def test_shortcut_failure_is_warning_after_successful_install(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(package, 'install', return_value=root / 'versions/0.5.0'), \
                 patch.object(bootstrap, 'create_shortcut', side_effect=OSError('synthetic shortcut failure')):
                bootstrap.main(['install', '--root', str(root), '--no-launch'])


if __name__ == '__main__':
    unittest.main()
