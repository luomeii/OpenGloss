"""Actual candidate Engine/HTTP budget contracts with synthetic SQLite only."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SOURCE = Path(__file__).resolve().parents[1]
for key in list(os.environ):
    if key.startswith('PAE_') or any(t in key.upper() for t in ('API_KEY','TOKEN','SECRET')):
        os.environ.pop(key,None)
sys.path.insert(0,str(SOURCE))
from pydantic import ValidationError
from pae_core import api, engine
from pae_core.contacts import CONTACT_SELECTED


class BudgetTests(unittest.TestCase):
    def setUp(self):
        art=ROOT/'test-artifacts'/'budget'
        art.mkdir(parents=True,exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(prefix='budget-',dir=art)
        work=Path(self.tmp.name)
        dictionary=work/'dictionary.sqlite'
        self.words=['budgetword'+c for c in 'abcdefghijklmnop']
        with closing(sqlite3.connect(dictionary)) as db:
            db.execute('CREATE TABLE dict_words(word TEXT PRIMARY KEY,translation TEXT,bnc INTEGER)')
            db.executemany('INSERT INTO dict_words VALUES (?,?,?)',[(w,'n. 测试词',0) for w in self.words])
            db.execute('CREATE TABLE exchange_map(inflected TEXT,base TEXT)');db.commit()
        lemma=work/'lemma.txt'
        lemma.write_text('\n'.join(w+'/1 -> '+w+'s' for w in self.words),encoding='utf-8')
        self.dict_patch=patch.object(engine,'DICT_SQLITE_PATH',str(dictionary));self.dict_patch.start()
        self.net_patch=patch('urllib.request.urlopen',side_effect=AssertionError('no model networking'));self.net_patch.start()
        self.e=engine.Engine(work/'learning.sqlite',lemma)
        self.e._params_ts=self.e._fam_ts=self.e._proactive_dispatch_ts=10**12
        self.e._fam_mode='shadow'

    def tearDown(self):
        self.e._close_translation_lookup()
        self.e.learning_conn.close();self.e.conn.close()
        self.net_patch.stop();self.dict_patch.stop();self.tmp.cleanup()

    def count(self):
        return self.e.conn.execute("SELECT COUNT(*) FROM events WHERE type=?",
                                   (CONTACT_SELECTED,)).fetchone()[0]

    def call(self,budget):
        try:
            return self.e.annotate(' '.join(self.words),'fixture','s0',max_annotations=budget)
        except TypeError as error:
            self.fail('optional annotation budget is not implemented: '+str(error))

    def test_default_budget_preserves_existing_limit(self):
        self.assertEqual(len(self.e.annotate(' '.join(self.words),'fixture','s0')),engine.BUDGET)

    def test_zero_budget_has_no_learning_events(self):
        self.assertEqual(self.call(0),[]);self.assertEqual(self.count(),0)

    def test_two_slots_select_and_record_only_two(self):
        self.assertEqual(len(self.call(2)),2);self.assertEqual(self.count(),2)

    def test_one_slot_selects_only_one(self):
        self.assertEqual(len(self.call(1)),1);self.assertEqual(self.count(),1)

    def test_request_cannot_raise_engine_budget(self):
        self.assertEqual(len(self.call(1000)),engine.BUDGET)
        self.assertEqual(self.count(),engine.BUDGET)

    def test_invalid_http_budgets_are_rejected(self):
        for value in (-1,True,1.5,'2'):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    api.AnnotateBody(text='fixture',max_annotations=value)

    def test_http_forwards_budget_and_preserves_list(self):
        with patch.object(api,'get_engine',return_value=self.e):
            result=api.annotate(api.AnnotateBody(text=' '.join(self.words),max_annotations=2))
        self.assertIsInstance(result,list);self.assertEqual(len(result),2);self.assertEqual(self.count(),2)

    def test_zero_budget_does_not_bypass_storage_fault(self):
        self.e._learning_write_fault=True
        with patch.object(api,'get_engine',return_value=self.e):
            result=api.annotate(api.AnnotateBody(text='fixture',max_annotations=0))
        self.assertEqual(result.status_code,503)
        self.assertEqual(self.count(),0)


if __name__=='__main__':unittest.main(verbosity=2)
