from pathlib import Path
import subprocess,sys,tempfile,unittest,sqlite3,contextlib
ROOT=Path(__file__).resolve().parents[2]
class EngineLifetime(unittest.TestCase):
 def test_engine_owner_cannot_return_while_background_sqlite_writer_is_alive(self):
  with tempfile.TemporaryDirectory(prefix='owner-lifetime-',dir=None) as temp:
   script=Path(temp)/'child.py';script.write_text('''import sys,threading,time,atexit,sqlite3
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from distribution import bootstrap
root=Path(sys.argv[2]);ready=threading.Event()
def backend(*args):
 def writer():
  c=sqlite3.connect(root/'synthetic.db');c.execute('create table synthetic(n integer)');c.commit();ready.set()
  while True:c.execute('insert into synthetic values(1)');c.commit();time.sleep(.005)
 threading.Thread(target=writer,daemon=True).start();ready.wait()
bootstrap._engine_main=backend
atexit.register(lambda:time.sleep(.3))
bootstrap.engine_main(root,root)
print('returned while background writer alive',flush=True)
''',encoding='utf-8')
   result=subprocess.run([sys.executable,'-I','-B',str(script),str(ROOT),temp],capture_output=True,text=True,timeout=10)
   self.assertEqual(result.returncode,0,result.stderr);self.assertNotIn('returned while background writer alive',result.stdout)
   with contextlib.closing(sqlite3.connect(Path(temp)/'synthetic.db')) as conn:self.assertEqual(conn.execute('pragma integrity_check').fetchone()[0],'ok')
if __name__=='__main__':unittest.main(verbosity=2)
