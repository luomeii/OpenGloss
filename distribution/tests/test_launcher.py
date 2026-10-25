from pathlib import Path
import os,sys,tempfile,unittest
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
try:
 from distribution import bootstrap,network
except ImportError:bootstrap=network=None
class LauncherContracts(unittest.TestCase):
 def test_launcher_and_network_features_exist(self):self.assertIsNotNone(bootstrap);self.assertIsNotNone(network)
 def test_environment_does_not_inherit_keys_proxies_or_other_engine_paths(self):
  if bootstrap is None:self.skipTest('launcher missing')
  with tempfile.TemporaryDirectory(dir=None) as temp:
   from unittest.mock import patch
   with patch.dict(os.environ,{'PAE_DB_PATH':'synthetic-other.db','PAE_LLM_KEY':'synthetic-test-value','HTTPS_PROXY':'http://invalid.test','PYTHONPATH':'synthetic-other'},clear=False):
    result=bootstrap.engine_environment(Path(temp)/'version',Path(temp)/'data')
   self.assertNotIn('PAE_LLM_KEY',result);self.assertNotIn('HTTPS_PROXY',result);self.assertNotIn('PYTHONPATH',result);self.assertEqual(result['PAE_DB_PATH'],str(Path(temp)/'data/pae.db'))
 def test_network_rejects_nonofficial_or_nonhttps_redirects(self):
  if network is None:self.skipTest('network module missing')
  for url in ['http://github.com/luomeii/OpenGloss','https://github.com.evil.test/file','file:///synthetic','https://127.0.0.1:4815','https://user:pass@github.com/file']:
   with self.subTest(url=url):self.assertRaises(ValueError,network.allowed_url,url)
  self.assertTrue(network.allowed_url('https://release-assets.githubusercontent.com/synthetic'))
 def test_mutations_keep_local_origin_boundary_without_tokens(self):
  self.assertTrue(bootstrap.trusted_management({},54936))
  self.assertTrue(bootstrap.trusted_management({'Origin':'http://127.0.0.1:54936'},54936))
  for headers in [{'Origin':'https://evil.test'},{'Origin':'null'},{'Sec-Fetch-Site':'cross-site'}]:self.assertFalse(bootstrap.trusted_management(headers,54936))
 def test_shutdown_admission_prevents_future_jobs_and_rejects_while_busy(self):
  if bootstrap is None:self.skipTest('launcher missing')
  import json
  from distribution import package
  with tempfile.TemporaryDirectory(dir=None) as temp:
   root=Path(temp);package.atomic_json(root/'installation.json',{'product':'OpenGloss','instance_id':'a'*32,'engine_port':54935,'manager_port':54936});supervisor=bootstrap.Supervisor(root)
   supervisor.busy=True;self.assertFalse(supervisor.admit_shutdown());supervisor.busy=False
   self.assertTrue(supervisor.admit_shutdown());self.assertRaises(RuntimeError,supervisor.submit,'check')
 def test_restore_refuses_data_owned_by_a_process_that_has_not_listened(self):
  if bootstrap is None:self.skipTest('launcher missing')
  import subprocess,json
  from distribution import package
  with tempfile.TemporaryDirectory(dir=None) as temp:
   root=Path(temp);(root/'data').mkdir();(root/'data/persona.json').write_text('{"name":"old"}');backup=package.snapshot_data(root);(root/'data/persona.json').write_text('{"name":"live"}')
   code="import sys;sys.path.insert(0,sys.argv[1]);from distribution.locks import data_owner;from pathlib import Path;import sqlite3;root=Path(sys.argv[2]);lock=data_owner(root);lock.__enter__();conn=sqlite3.connect(root/'data/pae.db');print('owned',flush=True);sys.stdin.readline();conn.close();lock.__exit__(None,None,None)"
   child=subprocess.Popen([sys.executable,'-I','-B','-c',code,str(ROOT),str(root)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
   try:
    self.assertEqual(child.stdout.readline().strip(),'owned');self.assertRaises(RuntimeError,package.restore_data,root,backup);self.assertEqual(json.loads((root/'data/persona.json').read_text())['name'],'live')
   finally:
    child.stdin.write('done\n');child.stdin.flush();child.wait(timeout=5);child.stdin.close();child.stdout.close();child.stderr.close()
   package.restore_data(root,backup);self.assertEqual(json.loads((root/'data/persona.json').read_text())['name'],'old')
 def test_queued_action_does_not_run_after_manager_finalization(self):
  if bootstrap is None:self.skipTest('launcher missing')
  from distribution import package
  from unittest.mock import patch
  with tempfile.TemporaryDirectory(dir=None) as temp:
   root=Path(temp);package.atomic_json(root/'installation.json',{'product':'OpenGloss','instance_id':'a'*32,'engine_port':54935,'manager_port':54936});supervisor=bootstrap.Supervisor(root);effects=[];queued=[]
   class DeferredThread:
    def __init__(self,*,target,daemon):queued.append(target)
    def start(self):pass
   supervisor.perform_update=lambda:effects.append('update executed')
   with patch.object(bootstrap.threading,'Thread',DeferredThread):supervisor.submit('update')
   supervisor.closing=True;queued[0]();self.assertEqual(effects,[])
 def test_reopen_refuses_session_url_outside_its_installation(self):
  if bootstrap is None:self.skipTest('launcher missing')
  from distribution import package
  from unittest.mock import patch
  with tempfile.TemporaryDirectory(dir=None) as temp:
   root=Path(temp);package.atomic_json(root/'installation.json',{'product':'OpenGloss','instance_id':'a'*32,'engine_port':54935,'manager_port':54936});package.atomic_json(root/'manager-session.json',{'instance_id':'a'*32,'token':'synthetic','url':'https://invalid.test'})
   with patch.object(bootstrap.urllib.request,'build_opener',side_effect=AssertionError('no network expected')):self.assertFalse(bootstrap.reopen_existing(root,no_browser=True))
 def test_same_version_reinstall_accepts_authenticated_manager_even_if_new_process_exits_zero(self):
  if bootstrap is None:self.skipTest('launcher missing')
  import io,json,contextlib,types
  from distribution import package
  from unittest.mock import patch
  with tempfile.TemporaryDirectory(dir=None) as temp:
   root=Path(temp);package.atomic_json(root/'installation.json',{'product':'OpenGloss','instance_id':'a'*32,'engine_port':54935,'manager_port':54936});package.atomic_json(root/'manager-session.json',{'instance_id':'a'*32,'token':'synthetic','url':'http://127.0.0.1:54936'})
   class Opener:
    def open(self,*a,**kw):return io.BytesIO(json.dumps({'engine_running':True,'stage':'ready','instance_id':'a'*32}).encode())
   with patch.object(package,'install',return_value=root/'versions/0.4.0-rc.1'),patch.object(bootstrap.subprocess,'Popen',return_value=types.SimpleNamespace(poll=lambda:0)),patch.object(bootstrap.urllib.request,'build_opener',return_value=Opener()),contextlib.redirect_stdout(io.StringIO()):bootstrap.main(['install','--root',str(root),'--no-shortcuts','--no-browser'])
 def test_default_localappdata_install_does_not_require_home_directory(self):
  if bootstrap is None:self.skipTest('launcher missing')
  import io,contextlib
  from distribution import package
  from unittest.mock import patch
  with tempfile.TemporaryDirectory(dir=None) as temp:
   with patch.dict(os.environ,{'LOCALAPPDATA':temp},clear=True),patch.object(bootstrap.Path,'home',side_effect=AssertionError('home must not be evaluated')),patch.object(package,'install',return_value=Path(temp)/'OpenGloss/versions/0.4.0-rc.1') as install,contextlib.redirect_stdout(io.StringIO()):
    bootstrap.main(['install','--no-shortcuts','--no-launch'])
    self.assertEqual(install.call_args.args[1],(Path(temp)/'OpenGloss').resolve())
if __name__=='__main__':unittest.main(verbosity=2)
