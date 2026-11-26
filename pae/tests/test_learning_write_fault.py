"""L11 commit-failure contract using only synthetic SQLite and a local engine."""
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

for name in list(os.environ):
    if name.startswith('PAE_') or any(s in name.upper() for s in ('API_KEY', 'TOKEN', 'SECRET')):
        os.environ.pop(name, None)
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pae_core import api, db
from pae_core.engine import Engine, LearningWritePaused
from pae_core.state import DEFAULTS


class FaultConnection(sqlite3.Connection):
    fail_commit = False

    def commit(self):
        if self.fail_commit == 'after':
            super().commit()
            raise sqlite3.OperationalError('synthetic acknowledgement loss')
        if self.fail_commit:
            raise sqlite3.OperationalError('synthetic commit failure')
        return super().commit()


class LearningWriteFaultTests(unittest.TestCase):
    def setUp(self):
        artifacts = Path(__file__).resolve().parents[3] / 'test-artifacts' / 'learning-write-fault'
        artifacts.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='synthetic-', dir=artifacts)
        self.path = Path(self.tmp.name) / 'events.sqlite'
        self.conn = sqlite3.connect(self.path, factory=FaultConnection)
        self.conn.row_factory = sqlite3.Row
        db.init_schema(self.conn)
        self.eng = Engine.__new__(Engine)
        self.eng.conn = self.conn
        self.eng.db_path = str(self.path)
        self.eng.lock = threading.Lock()
        self.eng.states = {}
        self.eng.seen_idem = set()
        self.eng.params = dict(DEFAULTS)
        self.eng.translations = {'synthetic': ('合成释义', 0)}
        self.eng.lemma_map = {}
        self.eng.exchange_map = {}
        self.eng._params_ts = self.eng._fam_ts = self.eng._proactive_dispatch_ts = 10**12
        self.eng._fam_mode = 'shadow'
        self.clock = patch('pae_core.engine.time.time', return_value=1700000000)
        self.clock.start()
        self.network = patch('urllib.request.urlopen', side_effect=AssertionError('network prohibited'))
        self.network.start()

    def tearDown(self):
        self.network.stop()
        self.clock.stop()
        self.conn.close()
        self.tmp.cleanup()

    def durable(self):
        with closing(sqlite3.connect(self.path)) as observer:
            return observer.execute('SELECT COUNT(*) FROM events').fetchone()[0]

    def restart_engine_from_log(self):
        self.conn.close()
        self.conn = sqlite3.connect(self.path, factory=FaultConnection)
        self.conn.row_factory = sqlite3.Row
        self.eng.conn = self.conn
        self.eng.states = {}
        self.eng.seen_idem = set()
        self.eng._learning_write_fault = False
        self.eng._replay()

    def test_normal_annotate_and_hover_stay_available(self):
        result = self.eng.annotate('synthetic', 'page-normal', 'session-normal')
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]['counted'])
        self.assertTrue(self.eng.record_event('hover', 'synthetic', metas={'session_id': 'session-normal'})['ok'])
        self.assertEqual(self.durable(), 2)
        self.assertFalse(getattr(self.eng, '_learning_write_fault', False))

    def test_annotate_commit_failure_is_not_acknowledged_or_later_committed(self):
        self.conn.fail_commit = True
        with self.assertRaises(LearningWritePaused):
            self.eng.annotate('synthetic', 'page-fault', 'session-fault')
        self.assertEqual(self.durable(), 0)
        self.assertTrue(self.eng._learning_write_fault)
        self.conn.fail_commit = False
        self.assertFalse(self.eng.record_event('annotation_shown', 'synthetic')['ok'])
        self.assertEqual(self.durable(), 0)
        with self.assertRaises(LearningWritePaused):
            self.eng.annotate('synthetic', 'page-next', 'session-next')
        self.restart_engine_from_log()
        self.assertNotIn(('synthetic', 0), self.eng.states)
        retried = self.eng.annotate('synthetic', 'page-fault', 'session-fault')
        self.assertTrue(retried[0]['counted'])
        self.assertEqual(self.durable(), 1)

    def test_annotate_acknowledgement_loss_requires_recovery(self):
        self.conn.fail_commit = 'after'
        with self.assertRaises(LearningWritePaused):
            self.eng.annotate('synthetic', 'page-ack-loss', 'session-ack-loss')
        self.assertEqual(self.durable(), 1)
        self.assertTrue(self.eng._learning_write_fault)
        self.restart_engine_from_log()
        self.assertIn(('synthetic', 0), self.eng.states)
        repeated = self.eng.annotate('synthetic', 'page-ack-loss', 'session-ack-loss')
        self.assertFalse(repeated[0]['counted'])
        self.assertEqual(self.durable(), 1)

    def test_hover_commit_failure_is_not_acknowledged(self):
        self.conn.fail_commit = True
        result = self.eng.record_event('hover', 'synthetic', metas={'session_id': 'session-fault'})
        self.assertEqual(result['ok'], False)
        self.assertEqual(result['counted'], False)
        self.assertEqual(result['error'], 'learning_write_paused')
        self.assertTrue(result['result_unknown'])
        self.assertEqual(self.durable(), 0)
        self.assertTrue(self.eng._learning_write_fault)

    def test_shown_acknowledgement_loss_is_not_acknowledged(self):
        self.conn.fail_commit = 'after'
        result = self.eng.record_event('annotation_shown', 'synthetic', metas={'page_id': 'page-fault'})
        self.assertEqual(result['ok'], False)
        self.assertEqual(result['counted'], False)
        self.assertTrue(result['result_unknown'])
        self.assertEqual(self.durable(), 1)
        self.assertTrue(self.eng._learning_write_fault)

    def test_api_routes_expose_failed_annotation_and_event(self):
        self.conn.fail_commit = True
        with patch.object(api, 'get_engine', return_value=self.eng):
            response = api.annotate(api.AnnotateBody(text='synthetic', page_id='page-api'))
            event = api.record_event(api.EventBody(type='hover', lemma='synthetic'))
        self.assertEqual(response.status_code, 503)
        self.assertFalse(event['ok'])
        self.assertEqual(event['error'], 'learning_write_paused')
        self.assertEqual(self.durable(), 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
