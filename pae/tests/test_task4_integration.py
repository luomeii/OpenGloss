"""Task 4 bounded backend contracts; offline synthetic data only."""
import concurrent.futures
import json
import unittest
from unittest.mock import patch

import test_security_contracts as base
from pae_core import capsurface, familiarity, memory, metrics, privacy
from pae_core.db import append_event


class Task4Contracts(unittest.TestCase):
    setUp = base.Contracts.setUp
    tearDown = base.Contracts.tearDown
    config = base.Contracts.config
    http = base.Contracts.http
    call = base.Contracts.call

    def candidates(self):
        words = 'vessel planet basket canyon dragon ember forest garden harbor island'.split()
        self.eng.translations.update({word: (word + '-gloss', 0) for word in words})
        return ' '.join(words)

    def encounters(self):
        return self.eng.conn.execute("SELECT COUNT(*) FROM events WHERE type='encounter'").fetchone()[0]

    def test_optional_annotation_limit_applies_before_encounter_on_all_surfaces(self):
        text = self.candidates()
        self.assertEqual(self.eng.annotate(text, 'direct-zero', max_annotations=0), [])
        self.assertEqual(self.http('/v1/annotate', method='POST', body={'text': text, 'page_id': 'http-zero', 'max_annotations': 0}), (200, []))
        s, body = self.call('pae.annotate', {'text': text, 'page_id': 'cap-zero', 'max_annotations': 0})
        self.assertEqual(s, 200, body)
        self.assertEqual(body['result'], [])
        self.assertEqual(self.encounters(), 0)
        for surface in ('direct', 'http', 'cap'):
            page = surface + '-two'
            if surface == 'direct': result = self.eng.annotate(text, page, max_annotations=2)
            elif surface == 'http': result = self.http('/v1/annotate', method='POST', body={'text': text, 'page_id': page, 'max_annotations': 2})[1]
            else: result = self.call('pae.annotate', {'text': text, 'page_id': page, 'max_annotations': 2})[1]['result']
            self.assertEqual(len(result), 2, surface)
        self.assertEqual(self.encounters(), 6)
        self.assertEqual(len(self.eng.annotate(text, 'large', max_annotations=999)), base.em.BUDGET)
        default = self.eng.annotate(text, 'default')
        self.assertEqual(len(default), base.em.BUDGET)
        self.assertEqual(self.encounters(), 6 + 2 * base.em.BUDGET)

    def test_annotation_limit_rejects_bool_float_string_and_negative_without_events(self):
        text = self.candidates()
        for invalid in (True, 2.0, '2', -1):
            with self.subTest(invalid=invalid):
                with self.assertRaises((TypeError, ValueError)):
                    self.eng.annotate(text, 'direct-invalid', max_annotations=invalid)
                self.assertNotEqual(self.http('/v1/annotate', method='POST', body={'text': text, 'page_id': 'http-invalid', 'max_annotations': invalid})[0], 200)
                self.assertNotEqual(self.call('pae.annotate', {'text': text, 'page_id': 'cap-invalid', 'max_annotations': invalid})[0], 200)
        self.assertEqual(self.encounters(), 0)

    def test_metrics_use_current_readonly_page_reference_and_label_cumulative_proxy(self):
        self.eng.conn.execute("INSERT INTO params VALUES('max_ann_per_page','12',0,'synthetic',NULL,NULL)")
        self.eng.conn.commit()
        empty = metrics.compute(self.eng.conn)
        self.assertEqual(empty['budget_utilization'], 0)
        self.assertEqual(empty['budget_denominator'], 12)
        url = 'https://synthetic.invalid/repeated'
        for index in range(18):
            append_event(self.eng.conn, 'annotation_shown', {'lemma': 'vessel', 'page_id': url}, idem_key='shown-'+str(index))
        trace = []
        self.eng.conn.set_trace_callback(trace.append)
        try: report = metrics.compute(self.eng.conn)
        finally: self.eng.conn.set_trace_callback(None)
        self.assertEqual(report['avg_annotations_per_page'], 18)
        self.assertEqual(report['budget_denominator'], 12)
        self.assertEqual(report['budget_utilization'], 1.0)
        self.assertIn('cumulative', report['budget_basis'].lower())
        self.assertNotIn('直接证据', report['note'])
        writes = [sql for sql in trace if sql.lstrip().upper().startswith(('INSERT','UPDATE','DELETE','CREATE','REPLACE'))]
        self.assertEqual(writes, [])

    def test_legacy_page_resolution_reads_secret_once_per_query_and_groups_like_hmac(self):
        url = 'https://synthetic.invalid/legacy'
        for index in range(12):
            append_event(self.eng.conn, 'annotation_shown', {'lemma': 'vessel', 'page_id': url, 'page_active_ms': 2000}, idem_key='legacy-'+str(index))
        append_event(self.eng.conn, 'annotation_shown', {'lemma': 'vessel', 'page_id': '', 'page_key': privacy.page_key(url), 'page_active_ms': 2000}, idem_key='modern')
        original = privacy.load_or_create
        with patch.object(privacy, 'load_or_create', wraps=original) as loaded:
            report = metrics.compute(self.eng.conn)
            self.assertEqual(loaded.call_count, 1)
        self.assertEqual(report['avg_annotations_per_page'], 13)
        with patch.object(privacy, 'load_or_create', wraps=original) as loaded:
            self.assertEqual(familiarity._exposure_stats(self.eng.conn)['vessel']['q_exp'], 1)
            self.assertEqual(loaded.call_count, 1)
        with patch.object(privacy, 'load_or_create', wraps=original) as loaded:
            self.assertEqual(metrics._outcome_inputs(self.eng.conn)[-1], 1)
            self.assertEqual(loaded.call_count, 1)

    def test_persona_ids_reference_committed_pair_and_identical_concurrent_requests(self):
        def respond(*args, **kwargs): return {'content': 'synthetic answer', 'model': 'fake'}
        with patch('pae_core.llm.chat', side_effect=respond):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(lambda _: self.call('pae.persona.say', {'stimulus': 'synthetic question', 'context_block': 'synthetic'}), range(2)))
        self.assertTrue(all(status == 200 for status, _ in responses), responses)
        pair_ids = []
        for _, response in responses:
            result = response['result']
            sid, uid, aid = (result[key] for key in ('session_id','user_turn_id','agent_turn_id'))
            pair_ids.append((uid, aid))
            rows = self.eng.conn.execute('SELECT turn_id, role, text FROM chat_turns WHERE turn_id IN (?,?)', (uid, aid)).fetchall()
            self.assertEqual({(role, text) for _, role, text in rows}, {('user','synthetic question'),('agent','synthetic answer')})
            self.assertEqual(self.eng.conn.execute('SELECT COUNT(*) FROM chat_turns WHERE session_id=? AND turn_id IN (?,?)', (sid, uid, aid)).fetchone()[0], 2)
        self.assertEqual(len({turn for pair in pair_ids for turn in pair}), 4)
        self.assertEqual(self.eng.conn.execute('SELECT COUNT(*) FROM chat_turns').fetchone()[0], 4)

    def test_persona_failed_second_turn_rolls_back_pair_and_fact(self):
        original = memory.session_turn
        def fail_agent(conn, sid, role, text, *args, **kwargs):
            if role == 'agent': raise RuntimeError('synthetic insert failure')
            return original(conn, sid, role, text, *args, **kwargs)
        with patch('pae_core.llm.chat', return_value={'content':'synthetic answer'}), patch.object(memory, 'session_turn', side_effect=fail_agent):
            status, body = self.call('pae.persona.say', {'stimulus':'记住我喜欢合成测试', 'context_block':'synthetic'})
        self.assertNotEqual(status, 200, body)
        self.assertEqual(self.eng.conn.execute('SELECT COUNT(*) FROM chat_turns').fetchone()[0], 0)
        self.assertEqual(self.eng.conn.execute('SELECT COUNT(*) FROM facts').fetchone()[0], 0)


if __name__ == '__main__': unittest.main()
