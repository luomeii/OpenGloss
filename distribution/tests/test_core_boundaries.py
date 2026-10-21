from pathlib import Path
import json,sys,sqlite3,tempfile,unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'pae'))
from pae_core import memory,capsurface,params,db,health
class ReleaseCoreContracts(unittest.TestCase):
 def conn(self):
  c=db.get_db(':memory:');db.init_schema(c);self.addCleanup(c.close);return c
 def test_invalid_conversation_does_not_create_orphan_reply(self):
  c=self.conn()
  with self.assertRaises(ValueError):capsurface._remember_turns(c,'u'*2001,'short answer')
  self.assertEqual(c.execute('select count(*) from chat_turns').fetchone()[0],0)
  self.assertEqual(c.execute('select count(*) from chat_sessions').fetchone()[0],0)
 def test_conversation_pair_rollback_on_second_insert_failure(self):
  c=self.conn();c.execute("CREATE TRIGGER reject_agent BEFORE INSERT ON chat_turns WHEN NEW.role='agent' BEGIN SELECT RAISE(ABORT,'synthetic failure'); END");c.commit()
  with self.assertRaises(Exception):capsurface._remember_turns(c,'question','answer')
  self.assertEqual(sqlite3.Connection.execute(c,'select count(*) from chat_turns').fetchone()[0],0)
  self.assertFalse(c.in_transaction)
 def test_fact_insert_and_evict_are_atomic(self):
  c=self.conn()
  for n in range(8):self.assertTrue(memory.remember(c,'companion','goal','synthetic %d'%n,['synthetic'])[0])
  c.execute("CREATE TRIGGER reject_evict BEFORE UPDATE ON facts BEGIN SELECT RAISE(ABORT,'synthetic eviction failure'); END");c.commit()
  with self.assertRaises(Exception):memory.remember(c,'companion','goal','synthetic ninth',['synthetic'])
  self.assertEqual(sqlite3.Connection.execute(c,'select count(*) from facts where superseded_by is null').fetchone()[0],8)
  self.assertFalse(c.in_transaction)
 def test_expiry_does_not_delete_replacement(self):
  with tempfile.TemporaryDirectory(prefix='release-ttl-',dir=None) as tmp:
   path=Path(tmp)/'synthetic.db';a=db.get_db(path);b=db.get_db(path)
   try:
    db.init_schema(a);a.execute("INSERT INTO params(key,value,actor,decision_id,expires_at,updated_at) VALUES('budget','8','local','old',1,0)");a.commit()
    class Interleave:
     def __getattr__(self,k):return getattr(a,k)
     def execute(self,sql,args=()):
      cursor=a.execute(sql,args)
      if sql.startswith('SELECT key, decision_id'):
       rows=cursor.fetchall();b.execute("UPDATE params SET value='9',decision_id='new',expires_at=9999999999 WHERE key='budget'");b.commit();return iter(rows)
      return cursor
    changed=params.revert_expired(Interleave())
    self.assertEqual(tuple(a.execute("SELECT value,decision_id FROM params WHERE key='budget'").fetchone()),('9','new'))
    self.assertEqual(changed,[])
   finally:a.close();b.close()
if __name__=='__main__':unittest.main(verbosity=2)


