from pathlib import Path
import hashlib,json,sys,tempfile,unittest,zipfile
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
try:
 from distribution import package
except ImportError:
 package=None
class DistributionContracts(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix='distribution-contract-',dir=None);self.addCleanup(self.tmp.cleanup);self.base=Path(self.tmp.name)
 def fixture(self,version='0.4.0-rc.1',extra=None):
  folder=self.base/version;folder.mkdir();files={'runtime/python.exe':b'not executed in this contract','runtime/pythonw.exe':b'not executed in this contract','distribution/bootstrap.py':b'# synthetic fixture\n','app/pae/config.json':b'{"agent_schedule":{"enabled":false}}','app/pae/persona/default.json':b'{"name":"synthetic"}'};files.update(extra or {})
  for name,data in files.items():p=folder/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
  (folder/'RELEASE-MANIFEST.json').write_text(json.dumps({'format':1,'product':'OpenGloss','version':version,'platform':'windows-x64','files':{k:{'sha256':hashlib.sha256(v).hexdigest(),'size':len(v)} for k,v in files.items()}}),encoding='utf-8');return folder
 def test_feature_exists(self):self.assertIsNotNone(package,'distribution package lifecycle is not implemented')
 def need(self):
  if package is None:self.skipTest('feature missing; first contract proves RED')
 def test_clean_install_creates_separate_data_and_keeps_source_immutable(self):
  self.need();src=self.fixture();root=self.base/'installed';package.install(src,root,engine_port=54935);self.assertTrue((root/'data/config.json').is_file());self.assertFalse((src/'pae.db').exists());self.assertEqual(package.active_version(root),'0.4.0-rc.1')
 def test_reinstall_does_not_overwrite_user_settings(self):
  self.need();src=self.fixture();root=self.base/'installed';package.install(src,root,engine_port=54935);(root/'data/config.json').write_text('{"user_setting":true}');package.install(src,root,engine_port=54935);self.assertEqual(json.loads((root/'data/config.json').read_text()),{'user_setting':True})
 def test_reject_extra_runtime_database(self):
  self.need();src=self.fixture();(src/'pae.db').write_bytes(b'synthetic');self.assertRaises(ValueError,package.verify_directory,src)
 def test_reject_declared_private_database(self):
  self.need();src=self.fixture(extra={'app/pae/pae.db':b'synthetic'});self.assertRaises(ValueError,package.verify_directory,src)
 def test_reject_tampered_payload_before_creating_install(self):
  self.need();src=self.fixture();(src/'runtime/python.exe').write_bytes(b'changed');target=self.base/'installed';self.assertRaises(ValueError,package.install,src,target,engine_port=54935);self.assertFalse(target.exists())
 def test_reject_unrelated_install_directory(self):
  self.need();src=self.fixture();target=self.base/'unrelated';target.mkdir();(target/'keep.txt').write_text('keep');self.assertRaises(ValueError,package.install,src,target,engine_port=54935);self.assertEqual((target/'keep.txt').read_text(),'keep')
 def test_reject_traversal_zip(self):
  self.need();path=self.base/'bad.zip'
  with zipfile.ZipFile(path,'w') as z:z.writestr('../escape','bad')
  self.assertRaises(ValueError,package.unpack,path,self.base/'out');self.assertFalse((self.base/'escape').exists())
 def test_version_order_handles_rc_and_stable(self):
  self.need();self.assertLess(package.version_key('0.4.0-rc.2'),package.version_key('0.4.0'));self.assertLess(package.version_key('0.4.0'),package.version_key('0.10.0'));self.assertRaises(ValueError,package.version_key,'../../private')
 def test_release_selection_requires_matching_asset_digest_and_stable_version(self):
  self.need();release={'tag_name':'v0.4.0','draft':False,'prerelease':False,'body':'synthetic notes','assets':[{'name':'OpenGloss-windows-x64.zip','size':100,'digest':'sha256:'+'a'*64,'browser_download_url':'https://github.com/luomeii/OpenGloss/releases/download/v0.4.0/OpenGloss-windows-x64.zip'}]};self.assertEqual(package.select_release(release,'0.3.1')['version'],'0.4.0');release['assets'][0].pop('digest');self.assertRaises(ValueError,package.select_release,release,'0.3.1')
 def test_successful_upgrade_preserves_synthetic_data(self):
  self.need();a=self.fixture();b=self.fixture('0.4.0');root=self.base/'installed';package.install(a,root,engine_port=54935);(root/'data/persona.json').write_text('{"name":"saved"}');package.upgrade(root,b,lambda v,d:True);self.assertEqual(package.active_version(root),'0.4.0');self.assertEqual(json.loads((root/'data/persona.json').read_text())['name'],'saved')
 def test_failed_upgrade_restores_pointer_and_user_files(self):
  self.need();a=self.fixture();b=self.fixture('0.4.0');root=self.base/'installed';package.install(a,root,engine_port=54935);(root/'data/persona.json').write_text('{"name":"saved"}')
  def fail(v,d):(d/'persona.json').write_text('{"name":"changed by failed startup"}');return False
  self.assertRaises(RuntimeError,package.upgrade,root,b,fail);self.assertEqual(package.active_version(root),'0.4.0-rc.1');self.assertEqual(json.loads((root/'data/persona.json').read_text())['name'],'saved')
 def test_user_rollback_restores_previous_program_and_database_snapshot(self):
  self.need();a=self.fixture();b=self.fixture('0.4.0');root=self.base/'installed';package.install(a,root,engine_port=54935);(root/'data/persona.json').write_text('{"name":"old"}');package.upgrade(root,b,lambda v,d:True);(root/'data/persona.json').write_text('{"name":"new"}');package.rollback(root,lambda v,d:True);self.assertEqual(package.active_version(root),'0.4.0-rc.1');self.assertEqual(json.loads((root/'data/persona.json').read_text())['name'],'old')
 def test_crash_journal_recovers_old_version_without_advancing_data(self):
  self.need();a=self.fixture();b=self.fixture('0.4.0');root=self.base/'installed';package.install(a,root,engine_port=54935);(root/'data/persona.json').write_text('{"name":"old"}');backup=package.snapshot_data(root);old=package.read_json(root/'active.json');package.stage(b,root);package.atomic_json(root/'pending-update.json',{'old':old,'new':'0.4.0','backup':str(backup.relative_to(root)).replace(chr(92),'/')});package.atomic_json(root/'active.json',{'version':'0.4.0','previous':'0.4.0-rc.1'});(root/'data/persona.json').write_text('{"name":"partial"}');self.assertTrue(package.recover_pending(root));self.assertEqual(package.active_version(root),'0.4.0-rc.1');self.assertEqual(json.loads((root/'data/persona.json').read_text())['name'],'old')
 def test_reinstall_different_version_requires_managed_update(self):
  self.need();a=self.fixture();b=self.fixture('0.4.0');root=self.base/'installed';package.install(a,root,engine_port=54935);self.assertRaises(ValueError,package.install,b,root,engine_port=54935);self.assertEqual(package.active_version(root),'0.4.0-rc.1')
if __name__=='__main__':unittest.main(verbosity=2)
