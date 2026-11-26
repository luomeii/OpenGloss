"""L11-R1: synthetic transaction and publication checks, never the daily DB."""
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
    if name.startswith('PAE_') or any(part in name.upper() for part in ('API_KEY', 'TOKEN', 'SECRET')):
        os.environ.pop(name, None)
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pae_core import api, capsurface, db, engine, observe, push
from pae_core.contacts import CONTACT_SELECTED
from pae_core.state import DEFAULTS


class ControlledConnection(sqlite3.Connection):
    failure = None
    commit_started = None
    allow_commit = None

    def commit(self):
        if self.commit_started is not None:
            self.commit_started.set()
            if not self.allow_commit.wait(2):
                raise AssertionError('synthetic commit gate timed out')
        if self.failure == 'before':
            raise sqlite3.OperationalError('synthetic precommit error')
        result = super().commit()
        if self.failure == 'after':
            raise sqlite3.OperationalError('synthetic acknowledgement loss')
        return result


class LearningTransactionTests(unittest.TestCase):
    def setUp(self):
        artifacts = Path(__file__).resolve().parents[3] / 'test-artifacts' / 'learning-transaction'
        artifacts.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='synthetic-', dir=artifacts)
        self.assertTrue(Path(self.tmp.name).resolve().is_relative_to(artifacts.resolve()))
        self.path = Path(self.tmp.name) / 'events.sqlite'
        self.main = sqlite3.connect(self.path, factory=ControlledConnection, check_same_thread=False)
        self.main.row_factory = sqlite3.Row
        db.init_schema(self.main)
        self.main.execute('PRAGMA journal_mode=WAL')
        self.learning = sqlite3.connect(self.path, factory=ControlledConnection, check_same_thread=False)
        self.learning.row_factory = sqlite3.Row
        self.learning.execute('PRAGMA journal_mode=WAL')
        self.eng = engine.Engine.__new__(engine.Engine)
        self.eng.conn = self.main
        self.eng.learning_conn = self.learning
        self.eng.db_path = str(self.path)
        self.eng.lock = threading.Lock()
        self.eng.states = {}
        self.eng.contacts = {}
        self.eng.seen_idem = set()
        self.eng.params = dict(DEFAULTS)
        self.eng.translations = {'synthetic': ('合成释义', 0), 'synthesizer': ('合成释义', 0)}
        self.eng.lemma_map = {}
        self.eng.exchange_map = {}
        self.eng._fam_mode = 'shadow'
        self.eng._params_ts = self.eng._fam_ts = self.eng._proactive_dispatch_ts = 10**12
        self.clock = patch.object(engine.time, 'time', return_value=1700000000)
        self.clock.start()
        self.engine_singleton = patch.object(engine, 'get_engine', return_value=self.eng)
        self.engine_singleton.start()
        self.network = patch('urllib.request.urlopen', side_effect=AssertionError('network prohibited'))
        self.network.start()

    def tearDown(self):
        self.network.stop()
        self.engine_singleton.stop()
        self.clock.stop()
        self.learning.close()
        self.main.close()
        self.tmp.cleanup()

    def durable(self, etype=CONTACT_SELECTED):
        with closing(sqlite3.connect(self.path, timeout=0.2)) as observer:
            return observer.execute('SELECT COUNT(*) FROM events WHERE type=?', (etype,)).fetchone()[0]

    def test_other_writer_cannot_commit_learning_batch(self):
        started = threading.Event()
        finished = threading.Event()
        observed = {}
        original_reduce = self.eng.reduce_chain
        calls = [0]

        def writer():
            started.set()
            try:
                push.notify(self.main, 'synthetic-probe', {'probe': True}, actor='probe')
            except Exception as exc:
                observed['writer_error'] = repr(exc)
            finally:
                finished.set()

        worker = threading.Thread(target=writer, daemon=True)

        def paused_reduce(token):
            calls[0] += 1
            if calls[0] == 4:
                worker.start()
                self.assertTrue(started.wait(1))
                observed['writer_finished_mid_request'] = finished.wait(0.15)
                observed['durable_mid_request'] = self.durable()
                self.learning.failure = 'before'
                if observed['writer_finished_mid_request']:
                    self.main.failure = 'before'
            return original_reduce(token)

        self.eng.reduce_chain = paused_reduce
        try:
            with self.assertRaises(engine.LearningWritePaused):
                self.eng.annotate('synthetic synthesizer', 'page', 'session')
            self.main.failure = None
            self.assertTrue(finished.wait(2), 'other writer remained blocked after failure cleanup')
            self.assertNotIn('writer_error', observed)
            self.assertEqual(observed['durable_mid_request'], 0)
            self.assertEqual(self.durable(), 0)
            self.assertEqual(self.eng.states, {})
            push.notify(self.main, 'synthetic-probe', {'after': True}, actor='probe')
            self.assertEqual(self.durable(), 0)
        finally:
            self.main.failure = None
            self.learning.failure = None
            if worker.ident is not None:
                worker.join(timeout=2)

    def test_pending_state_stays_invisible_until_commit(self):
        begun = threading.Event()
        release = threading.Event()
        self.learning.commit_started = begun
        self.learning.allow_commit = release
        result = {}
        worker = threading.Thread(target=lambda: result.setdefault(
            'annotations', self.eng.annotate('synthetic synthesizer', 'page', 'session')), daemon=True)
        worker.start()
        reader = None
        read_started = threading.Event()
        read_finished = threading.Event()

        def read_words():
            read_started.set()
            result['words'] = observe.band_words(self.eng)
            read_finished.set()

        try:
            self.assertTrue(begun.wait(2), 'learning connection did not reach commit')
            self.assertEqual(self.durable(), 0)
            self.assertEqual(self.eng.states, {})
            self.assertEqual(self.eng.seen_idem, set())
            self.assertEqual(self.eng.contacts, {})
            reader = threading.Thread(target=read_words, daemon=True)
            reader.start()
            self.assertTrue(read_started.wait(1))
            self.assertFalse(read_finished.wait(0.1))
        finally:
            release.set()
            worker.join(timeout=2)
            if reader is not None:
                reader.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertTrue(read_finished.is_set())
        self.assertEqual(self.durable(), 2)
        self.assertEqual(len(result['annotations']), 2)
        self.assertEqual(self.eng.states, {})
        self.assertEqual(len(self.eng.contacts), 2)
        self.assertEqual(len(result['words']), 2)
        self.assertFalse(getattr(self.eng, '_learning_write_fault', False))

    def test_capability_audit_cannot_commit_partial_learning_batch(self):
        started = threading.Event()
        finished = threading.Event()
        observed = {}
        original_reduce = self.eng.reduce_chain
        calls = [0]

        def audit_writer():
            started.set()
            capsurface.audit('local', 'pae.status', True)
            finished.set()

        worker = threading.Thread(target=audit_writer, daemon=True)

        def paused_reduce(token):
            calls[0] += 1
            if calls[0] == 4:
                worker.start()
                self.assertTrue(started.wait(1))
                observed['audit_finished_mid_request'] = finished.wait(0.15)
                observed['durable_mid_request'] = self.durable()
                self.learning.failure = 'before'
            return original_reduce(token)

        self.eng.reduce_chain = paused_reduce
        try:
            with self.assertRaises(engine.LearningWritePaused):
                self.eng.annotate('synthetic synthesizer', 'page-audit', 'session')
            self.assertTrue(finished.wait(2), 'audit stayed blocked after rollback')
            self.assertFalse(observed['audit_finished_mid_request'])
            self.assertEqual(observed['durable_mid_request'], 0)
            self.assertEqual(self.durable(), 0)
            self.assertIsNone(capsurface.LAST_AUDIT_ERROR)
        finally:
            self.learning.failure = None
            if worker.ident is not None:
                worker.join(timeout=2)

    def test_short_unrelated_write_lock_waits_then_commits(self):
        self.main.execute('BEGIN IMMEDIATE')
        started = threading.Event()
        finished = threading.Event()
        result = {}

        def annotate_worker():
            started.set()
            try:
                result['annotations'] = self.eng.annotate('synthetic', 'page-lock', 'session')
            except Exception as exc:
                result['error'] = repr(exc)
            finally:
                finished.set()

        worker = threading.Thread(target=annotate_worker, daemon=True)
        worker.start()
        try:
            self.assertTrue(started.wait(1))
            self.assertFalse(finished.wait(0.15))
            self.main.commit()
            self.assertTrue(finished.wait(2), 'learning writer did not resume after lock release')
            self.assertNotIn('error', result)
            self.assertEqual(self.durable(), 1)
        finally:
            self.main.rollback()
            worker.join(timeout=2)

    def test_acknowledgement_loss_does_not_publish_tentative_state(self):
        self.learning.failure = 'after'
        with self.assertRaises(engine.LearningWritePaused):
            self.eng.annotate('synthetic', 'page-ack', 'session')
        self.assertEqual(self.durable(), 1)
        self.assertEqual(self.eng.states, {})
        self.assertEqual(self.eng.contacts, {})
        self.assertTrue(self.eng._learning_write_fault)
        with self.assertRaises(engine.LearningWritePaused):
            self.eng.status()

    def test_event_commit_acknowledgement_loss_stays_unconfirmed(self):
        self.learning.failure = 'after'
        result = self.eng.record_event('known_click', 'synthetic')
        self.assertFalse(result['ok'])
        self.assertFalse(result['counted'])
        self.assertTrue(result['result_unknown'])
        self.assertEqual(self.durable('known_click'), 1)
        self.assertEqual(self.eng.states, {})
        self.assertTrue(self.eng._learning_write_fault)
        self.assertFalse(self.eng.feedback_state('synthetic')['ok'])
        with self.assertRaises(engine.LearningWritePaused):
            observe.band_words(self.eng)
        with patch.object(api, 'get_engine', return_value=self.eng):
            self.assertEqual(api.status().status_code, 503)
            self.assertEqual(api.feedback_state('synthetic').status_code, 503)
        with patch.object(capsurface, 'audit'), patch.object(engine, 'get_engine', return_value=self.eng):
            status, body = capsurface.handle('pae.status', {}, actor_hint='local')
            config_status, _ = capsurface.handle('pae.config', {}, actor_hint='local')
        self.assertEqual(status, 503)
        self.assertEqual(body['error'], 'learning_write_paused')
        self.assertEqual(config_status, 200)
        sent, push_result = push.say(self.main, 'companion', 'synthetic reminder',
                                     guard_engine=self.eng)
        self.assertFalse(sent)
        self.assertEqual(push_result['error'], 'learning_write_paused')
        self.assertEqual(self.main.execute('SELECT COUNT(*) FROM push_log').fetchone()[0], 0)
        self.assertEqual(push.maybe_proactive(self.main, self.eng)['reason'],
                         'learning_write_paused')

    def test_normal_event_publishes_after_commit_and_replay_matches(self):
        annotated = self.eng.annotate('synthetic', 'page-normal', 'session')
        self.assertTrue(annotated[0]['counted'])
        known = self.eng.record_event('known_click', 'synthetic')
        self.assertTrue(known['ok'])
        self.assertTrue(known['counted'])
        self.assertTrue(known['feedback']['recorded'])
        for event_type in ('hover', 'annotation_shown', 'user_pin', 'user_mastered'):
            result = self.eng.record_event(event_type, 'synthetic',
                                           metas={'page_id': 'page-' + event_type,
                                                  'session_id': 'session'})
            self.assertTrue(result['ok'], event_type)
            self.assertTrue(result['counted'], event_type)
        before = self.eng.states[('synthetic', 0)]
        self.assertEqual(before.n_enc, 0)
        self.assertEqual(self.durable(), 1)
        self.assertEqual(before.n_known, 1)
        replayed = engine.Engine.__new__(engine.Engine)
        replayed.conn = self.main
        replayed.params = self.eng.params
        replayed.states = {}
        replayed.seen_idem = set()
        replayed._replay()
        self.assertEqual(replayed.contacts, self.eng.contacts)
        self.assertEqual(replayed.seen_idem, self.eng.seen_idem)
        after = replayed.states[('synthetic', 0)]
        self.assertEqual((before.E, before.n_enc, before.n_hover, before.n_known,
                          before.t_known, before.pinned, before.mastered),
                         (after.E, after.n_enc, after.n_hover, after.n_known,
                          after.t_known, after.pinned, after.mastered))
        repeated = self.eng.record_event('known_click', 'synthetic')
        self.assertTrue(repeated['ok'])
        self.assertFalse(repeated['counted'])
        self.assertEqual(self.durable('known_click'), 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
