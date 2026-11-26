"""Feedback regression probes: synthetic SQLite only, no running engine."""
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

from pae_core import db
from pae_core.engine import Engine
from pae_core.fold import fold_event
from pae_core.state import DEFAULTS


class FaultConnection(sqlite3.Connection):
    fail_commit = False

    def commit(self):
        if self.fail_commit == 'after':
            super().commit()
            raise sqlite3.OperationalError('synthetic commit acknowledgement loss')
        if self.fail_commit:
            raise sqlite3.OperationalError('synthetic commit failure')
        return super().commit()


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        artifacts = Path(__file__).resolve().parents[3] / 'test-artifacts' / 'feedback'
        artifacts.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='synthetic-', dir=artifacts)
        self.path = Path(self.tmp.name) / 'synthetic.sqlite'
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
        self.eng.translations = {'synthetic':('合成释义',0)}
        self.eng.lemma_map = {}
        self.eng.exchange_map = {}
        self.eng._params_ts = self.eng._fam_ts = self.eng._proactive_dispatch_ts = 1700000000
        self.clock = patch('pae_core.engine.time.time', return_value=1700000000)
        self.clock.start()
        self.network = patch('urllib.request.urlopen', side_effect=AssertionError('network prohibited'))
        self.network.start()

    def tearDown(self):
        self.network.stop()
        self.clock.stop()
        self.conn.close()
        self.tmp.cleanup()

    def known(self):
        return self.eng.record_event('known_click', 'synthetic', metas={'page_id':'synthetic-page'})

    def test_normal_feedback_preserves_weight_and_survives_replay(self):
        first = self.known()
        self.assertTrue(first['ok'])
        self.assertTrue(first['counted'])
        self.assertEqual(self.eng.states[('synthetic', 0)].E, DEFAULTS['w_known'])
        replay = {}; seen = set()
        with closing(sqlite3.connect(self.path)) as observer:
            observer.row_factory = sqlite3.Row
            events = db.load_events(observer)
        self.assertEqual(len(events), 1)
        for ev in events:
            payload = dict(ev['payload'], ts=ev['ts'], type=ev['type'], idem_key=ev['idem_key'])
            fold_event(replay, payload, self.eng.params, seen)
        self.assertEqual(vars(replay[('synthetic', 0)]), vars(self.eng.states[('synthetic', 0)]))

    def test_same_bucket_does_not_increase_score_twice(self):
        self.known()
        second = self.known()
        self.assertTrue(second['ok'])
        self.assertFalse(second['counted'])
        self.assertEqual(self.eng.states[('synthetic', 0)].n_known, 1)
        self.assertEqual(self.eng.states[('synthetic', 0)].E, DEFAULTS['w_known'])

    def test_shared_transaction_contains_other_pending_events(self):
        payload = {'lemma_id':'other_synthetic', 'lemma':'other_synthetic', 'session_id':'s0'}
        db.append_event(self.conn, 'encounter', payload, idem_key='synthetic-pending', autocommit=False)
        fold_event(self.eng.states, dict(payload, type='encounter', ts=1700000000,
                   idem_key='synthetic-pending'), self.eng.params, self.eng.seen_idem)
        with closing(sqlite3.connect(self.path)) as observer:
            self.assertEqual(observer.execute('SELECT COUNT(*) FROM events').fetchone()[0], 0)
        self.assertTrue(self.known()['ok'])
        with closing(sqlite3.connect(self.path)) as observer:
            self.assertEqual(observer.execute('SELECT COUNT(*) FROM events').fetchone()[0], 2)

    def test_commit_failure_must_not_claim_success(self):
        self.conn.fail_commit = True
        result = self.known()
        with closing(sqlite3.connect(self.path)) as observer:
            durable = observer.execute('SELECT COUNT(*) FROM events').fetchone()[0]
        self.assertEqual(durable, 0)
        self.assertFalse(result['ok'], 'uncommitted event must not be acknowledged as saved')

    def test_commit_failure_must_not_publish_learning_state(self):
        self.conn.fail_commit = True
        self.known()
        st = self.eng.states.get(('synthetic', 0))
        self.assertTrue(st is None or st.n_known == 0,
                        'failed commit must not publish known evidence to in-memory state')

    def test_only_real_commit_failure_pauses_learning(self):
        for i in range(20):
            self.assertTrue(self.eng.record_event('known_click', 'synthetic-'+str(i))['ok'])
        self.assertFalse(getattr(self.eng, '_learning_write_fault', False))
        self.assertTrue(self.known()['ok'])
        self.assertTrue(self.known()['ok'])
        self.assertFalse(getattr(self.eng, '_learning_write_fault', False))
        self.conn.fail_commit = True
        self.assertFalse(self.known_new()['ok'])
        self.assertTrue(getattr(self.eng, '_learning_write_fault', False))
        before = self.conn.total_changes
        self.assertFalse(self.eng.record_event('known_click', 'after-fault')['ok'])
        self.assertEqual(self.conn.total_changes, before)
        with self.assertRaises(RuntimeError):
            self.eng.annotate('synthetic', 'after-fault', 's0')

    def known_new(self):
        return self.eng.record_event('known_click', 'synthetic-new')

    def test_quota_rejection_is_not_storage_failure(self):
        for i in range(60):
            self.assertTrue(self.eng.record_event('known_click', 'quota-'+str(i))['ok'])
        self.assertFalse(self.known()['ok'])
        self.assertFalse(getattr(self.eng, '_learning_write_fault', False))
        self.assertTrue(self.eng.record_event('known_click', 'quota-0')['ok'],
                        'an already recorded word must remain confirmed even at the daily limit')

    def test_feedback_state_uses_committed_event_and_same_bucket(self):
        state_fn = getattr(self.eng, 'feedback_state', None)
        self.assertTrue(callable(state_fn), 'feedback needs a canonical read path')
        self.assertFalse(state_fn('synthetic')['recorded'])
        self.known()
        state = state_fn('synthetic')
        self.assertTrue(state['recorded'])
        self.assertGreater(state['valid_until'], 1700000000)
        with patch('pae_core.engine.time.time', return_value=1700000000+86400):
            self.assertFalse(state_fn('synthetic')['recorded'])

    def test_restart_recovers_only_committed_state_after_fault(self):
        self.known()
        self.conn.fail_commit = True
        self.known_new()
        self.conn.close()
        self.conn = sqlite3.connect(self.path, factory=FaultConnection)
        self.conn.row_factory = sqlite3.Row
        recovered = {}; seen = set()
        for ev in db.load_events(self.conn):
            fold_event(recovered, dict(ev['payload'], ts=ev['ts'], type=ev['type'],
                       idem_key=ev['idem_key']), self.eng.params, seen)
        self.assertEqual(recovered[('synthetic',0)].n_known, 1)
        self.assertNotIn(('synthetic-new',0), recovered)

    def test_commit_acknowledgement_loss_recovers_without_duplicate(self):
        self.conn.fail_commit = 'after'
        result = self.known()
        self.assertFalse(result['ok'])
        self.assertTrue(result['result_unknown'])
        self.assertNotIn(('synthetic',0), self.eng.states)
        self.conn.close()
        self.conn = sqlite3.connect(self.path, factory=FaultConnection)
        self.conn.row_factory = sqlite3.Row
        self.eng.conn = self.conn
        self.eng.states = {}; self.eng.seen_idem = set()
        self.eng._learning_write_fault = False  # Simulate a newly constructed instance.
        self.eng._replay()
        self.assertEqual(self.eng.states[('synthetic',0)].n_known,1)
        self.assertTrue(self.known()['ok'])
        self.assertEqual(self.eng.states[('synthetic',0)].n_known,1)

    def test_inflight_annotation_checks_fault_before_its_commit(self):
        owner=self
        class InterleavedParams(dict):
            def get(self,key,default=None):
                if key=='annotate_max_s':
                    owner.conn.fail_commit=True
                    owner.assertFalse(owner.known_new()['ok'])
                return super().get(key,default)
        self.eng.params=InterleavedParams(DEFAULTS)
        with self.assertRaises(RuntimeError):
            self.eng.annotate('synthetic','synthetic-inflight','s0')
        with closing(sqlite3.connect(self.path)) as observer:
            self.assertEqual(observer.execute('SELECT COUNT(*) FROM events').fetchone()[0],0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
