from pathlib import Path
import json,sys,unittest
sys.path[:0]=[str(Path(__file__).resolve().parents[2]),str(Path(__file__).parent)]
from distribution import package
import test_package as fixtures

class RootEntryContracts(unittest.TestCase):
    def setUp(self):
        fixtures.DistributionContracts.setUp(self)
        self.source=fixtures.DistributionContracts.fixture(self,extra={
            'app/pae/extension/sw.js':b"const PAE_DEFAULT_BASE = 'http://127.0.0.1:4817';\n",
            'app/pae/extension/popup.js':b"const DEFAULT_BASE = 'http://127.0.0.1:4817';\n",
            'app/pae/extension/manifest.json':b'{"manifest_version":3,"name":"Synthetic","version":"0.4.0"}'})
        self.root=self.base/'installed';package.install(self.source,self.root,engine_port=54935)

    def test_readme_and_four_actions_are_at_install_root(self):
        for name in ('README.md','quick_start.bat','quick_stop.bat','autostart_on.bat','autostart_off.bat','Install.cmd'):
            self.assertTrue((self.root/name).is_file(),name+' missing from install root')

    def test_scripts_are_ascii_and_resolve_active_version_without_system_python(self):
        for name in ('quick_start.bat','quick_stop.bat','autostart_on.bat','autostart_off.bat'):
            text=(self.root/name).read_bytes().decode('ascii')
            self.assertIn('active.json',text);self.assertIn('%~dp0',text)
            self.assertNotIn('0.4.0',text);self.assertNotIn('taskkill',text)

    def test_installed_extension_uses_this_instance_port_without_user_input(self):
        self.assertIn('127.0.0.1:54935',(self.root/'extension/sw.js').read_text())
        self.assertIn('127.0.0.1:54935',(self.root/'extension/popup.js').read_text())

    def test_repeat_install_keeps_valid_root_entries_and_uninstalls_them(self):
        before={p.name:p.read_bytes() for p in self.root.glob('*.bat')}
        package.install(self.source,self.root,engine_port=54935)
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.root.glob('*.bat')})
        package.uninstall_files(self.root)
        self.assertFalse((self.root/'quick_start.bat').exists())
        self.assertFalse((self.root/'extension').exists())
        self.assertTrue((self.root/'data').is_dir())

    def test_modified_root_file_is_not_silently_overwritten_or_deleted(self):
        (self.root/'README.md').write_text('synthetic user-owned replacement')
        with self.assertRaises(ValueError):package.install(self.source,self.root,engine_port=54935)
        with self.assertRaises(ValueError):package.uninstall_files(self.root)
        self.assertEqual((self.root/'README.md').read_text(),'synthetic user-owned replacement')

    def newer(self):
        return fixtures.DistributionContracts.fixture(self,'0.4.0',extra={
            'README.md':b'# Synthetic new version\n',
            'app/pae/extension/sw.js':b"const PAE_DEFAULT_BASE='http://127.0.0.1:4817'; // new\n"})

    def test_upgrade_and_rollback_sync_visible_guide_and_extension(self):
        old=(self.root/'extension/sw.js').read_bytes()
        package.upgrade(self.root,self.newer(),lambda version,data:True)
        self.assertIn('Synthetic new version',(self.root/'README.md').read_text())
        self.assertIn('54935',(self.root/'extension/sw.js').read_text())
        package.rollback(self.root,lambda version,data:True)
        self.assertEqual((self.root/'extension/sw.js').read_bytes(),old)
        self.assertNotIn('Synthetic new version',(self.root/'README.md').read_text())

    def test_failed_probe_restores_root_entries_together_with_program(self):
        before={p.relative_to(self.root).as_posix():p.read_bytes() for p in [self.root/'README.md',self.root/'extension/sw.js']}
        with self.assertRaises(RuntimeError):package.upgrade(self.root,self.newer(),lambda version,data:False)
        for name,data in before.items():self.assertEqual((self.root/name).read_bytes(),data)
        self.assertFalse((self.root/'pending-update.json').exists())

    def test_unknown_empty_extension_directory_blocks_deletion(self):
        foreign=self.root/'extension/foreign';foreign.mkdir()
        with self.assertRaises(ValueError):package.uninstall_files(self.root)
        self.assertTrue(foreign.is_dir())

if __name__=='__main__':unittest.main()
