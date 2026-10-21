from pathlib import Path
import hashlib,json,sys,threading,unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from distribution import bootstrap,package

class ReviewBoundaryContracts(unittest.TestCase):
 def busy_probe(self,operation):
  supervisor=object.__new__(bootstrap.Supervisor)
  supervisor.mutex=threading.RLock();supervisor.busy=False;supervisor.closing=False;supervisor.state={}
  entered=threading.Event();release=threading.Event();done=threading.Event();result=[]
  def check():entered.set();release.wait(5)
  supervisor.check=check;supervisor.submit('check')
  self.assertTrue(entered.wait(2))
  def contender():
   try:
    if operation=='stop':result.append(supervisor.admit_shutdown())
    else:
     try:supervisor.submit('restart');result.append('accepted')
     except RuntimeError:result.append('busy')
   finally:done.set()
  thread=threading.Thread(target=contender);thread.start()
  try:
   self.assertTrue(done.wait(.4),'a busy manager must reject a second action without waiting for its network operation')
   self.assertEqual(result,[False] if operation=='stop' else ['busy'])
   self.assertFalse(supervisor.closing)
  finally:
   release.set();thread.join(3)
 def test_stop_rejected_promptly_during_real_busy_action(self):self.busy_probe('stop')
 def test_restart_rejected_promptly_during_real_busy_action(self):self.busy_probe('restart')
 def test_manifest_requires_background_launcher_for_future_restart(self):
  files={name:{'size':0,'sha256':hashlib.sha256(b'').hexdigest()} for name in ('runtime/python.exe','distribution/bootstrap.py','app/pae/config.json','app/pae/persona/default.json')}
  doc={'format':1,'product':'OpenGloss','platform':'windows-x64','version':'0.4.0','files':files}
  with self.assertRaisesRegex(ValueError,'Incomplete runtime package'):package.check_manifest(doc)
  files['runtime/pythonw.exe']={'size':0,'sha256':hashlib.sha256(b'').hexdigest()}
  self.assertIs(package.check_manifest(doc),doc)

if __name__=='__main__':unittest.main(verbosity=2)
