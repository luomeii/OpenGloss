"""Installer contracts; never start the engine or touch its normal port."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile

SOURCE = Path(__file__).resolve().parents[2]
BUILDER = SOURCE / 'distribution/build_release.py'


def digest(data):
    return hashlib.sha256(data).hexdigest()


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(BUILDER.is_file(), 'offline installer builder is not implemented')
        spec = importlib.util.spec_from_file_location('installer_builder', BUILDER)
        self.builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.builder)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.cache = self.root / 'inputs'
        self.source.mkdir()
        self.cache.mkdir()
        self.base = {'pae/config.json': b'{"budget":8}',
                     'pae/extension/sw.js': b'original capsule panel sidebar',
                     'pae/quick_start.bat': b'@echo off\r\ncd /d "%~dp0"\r\n',
                     'resources/ECDICT/lemma.en.txt': b'word -> word'}
        for name, data in self.base.items():
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        for name in ('Install.cmd', 'distribution/install.ps1', '安装说明.md'):
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((SOURCE / name).read_bytes())
        self.lock = {'schema': 1, 'baseline_commit': 'test-public-baseline',
                     'baseline_files': {n: digest(d) for n, d in self.base.items()}}
        for key, filename, members in (
                ('runtime', 'python.zip', {'python.exe': b'public runtime',
                                         'python312._pth': b'python312.zip\n.\n'}),
                ('wheel', 'demo.whl', {'demo/__init__.py': b'value=1\n'})):
            path = self.cache / filename
            with zipfile.ZipFile(path, 'w') as archive:
                for name, data in members.items():
                    archive.writestr(name, data)
            item = {'filename': filename, 'sha256': digest(path.read_bytes()),
                    'size': path.stat().st_size}
            self.lock['wheels' if key == 'wheel' else key] = [item] if key == 'wheel' else item
        data = b'public dictionary input'
        (self.cache / 'dict.sqlite').write_bytes(data)
        self.lock['dictionary'] = {'filename': 'dict.sqlite', 'sha256': digest(data), 'size': len(data)}
        self.save_lock()

    def save_lock(self):
        (self.source / 'distribution/inputs.lock.json').write_text(
            json.dumps(self.lock), encoding='utf-8')

    def build(self):
        output = self.root / 'output.zip'
        self.builder.build(self.source, self.cache, output, self.lock)
        return output

    def test_package_is_self_contained_and_original_files_unchanged(self):
        output = self.build()
        with zipfile.ZipFile(output) as archive:
            for name, data in self.base.items():
                self.assertEqual(archive.read(name), data)
                self.assertEqual((self.source / name).read_bytes(), data)
            self.assertEqual(archive.read('pae/python.exe'), b'public runtime')
            self.assertEqual(archive.read('pae/Lib/site-packages/demo/__init__.py'), b'value=1\n')
            self.assertEqual(archive.read('resources/ECDICT/dict.sqlite'), b'public dictionary input')
            self.assertIn('Lib/site-packages', archive.read('pae/python312._pth').decode())
            manifest = json.loads(archive.read('INSTALL-MANIFEST.json'))
            self.assertEqual(set(archive.namelist()), set(manifest['files']) | {'INSTALL-MANIFEST.json'})
            for name, value in manifest['files'].items():
                self.assertEqual(digest(archive.read(name)), value)

    def test_never_packages_private_data_or_git_metadata(self):
        for name in ('pae/pae.db', 'pae/keys.json', '.git/config', 'pae/__pycache__/private.pyc'):
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'private sentinel')
        with zipfile.ZipFile(self.build()) as archive:
            self.assertFalse(any(b'private sentinel' in archive.read(n) for n in archive.namelist()))

    def test_modified_original_is_rejected(self):
        (self.source / 'pae/config.json').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'baseline'):
            self.build()

    def test_missing_original_is_rejected(self):
        (self.source / 'pae/extension/sw.js').unlink()
        with self.assertRaisesRegex(ValueError, 'baseline'):
            self.build()

    def test_corrupt_public_input_is_rejected(self):
        (self.cache / 'dict.sqlite').write_bytes(b'broken download')
        with self.assertRaisesRegex(ValueError, 'input'):
            self.build()

    def test_wheel_traversal_cannot_escape_package(self):
        path = self.cache / 'demo.whl'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('../../../escape.txt', b'escape')
        self.lock['wheels'][0].update(sha256=digest(path.read_bytes()), size=path.stat().st_size)
        with self.assertRaisesRegex(ValueError, 'archive'):
            self.build()
        self.assertFalse((self.root / 'escape.txt').exists())

    def test_runtime_cannot_replace_original_engine(self):
        path = self.cache / 'python.zip'
        with zipfile.ZipFile(path, 'a') as archive:
            archive.writestr('config.json', b'overwrite')
        self.lock['runtime'].update(sha256=digest(path.read_bytes()), size=path.stat().st_size)
        with self.assertRaisesRegex(ValueError, 'collision'):
            self.build()

    def test_corrupt_install_stops_before_shortcuts_and_keeps_existing_data(self):
        target = self.root / '安装 test & folder'
        with zipfile.ZipFile(self.build()) as archive:
            archive.extractall(target)
        (target / 'pae/pae.db').write_bytes(b'existing private data')
        (target / 'pae/python.exe').write_bytes(b'tampered')
        result = subprocess.run(
            ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
             str(target / 'distribution/install.ps1')], capture_output=True, timeout=60)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'Integrity check failed', result.stdout + result.stderr)
        self.assertEqual((target / 'pae/pae.db').read_bytes(), b'existing private data')
        self.assertFalse(list(target.glob('*.lnk')))


if __name__ == '__main__':
    unittest.main(verbosity=2)
