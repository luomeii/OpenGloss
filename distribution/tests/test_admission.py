from pathlib import Path
import asyncio,sys,tempfile,unittest
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
try:from distribution.admission import Admission
except ImportError:Admission=None
class AdmissionContract(unittest.TestCase):
 def test_admission_feature_exists(self):self.assertIsNotNone(Admission)
 def test_pending_switch_blocks_business_but_allows_health_then_reopens(self):
  if Admission is None:self.skipTest('feature missing')
  with tempfile.TemporaryDirectory(dir=None) as temp:
   root=Path(temp);journal=root/'pending-update.json';journal.write_text('{}');effects=[]
   async def app(scope,receive,send):effects.append(scope['path']);await send({'type':'http.response.start','status':200,'headers':[]});await send({'type':'http.response.body','body':b'ok'})
   wrapper=Admission(app,root)
   async def request(path):
    messages=[]
    async def receive():return {'type':'http.request','body':b''}
    async def send(value):messages.append(value)
    await wrapper({'type':'http','path':path},receive,send);return messages[0]['status']
   self.assertEqual(asyncio.run(request('/v1/annotate')),503);self.assertEqual(effects,[]);self.assertEqual(asyncio.run(request('/v1/health')),200);journal.unlink();self.assertEqual(asyncio.run(request('/v1/annotate')),200)
if __name__=='__main__':unittest.main(verbosity=2)
