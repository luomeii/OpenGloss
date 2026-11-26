"""Context termination regressions: bounded synthetic fixtures, no server/model."""
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pae_core import observe, capsurface, llm
import test_http_write_source as fixture


class ContextBudgetUnitTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.words = [{'lemma': 'fixtureword', 'gloss': '合成释义', 's_value': 0.1}]
        self.metrics = {'annotation_exposure': 3, 'top_ignored_words': ['synthetic']}
        self.page = {'selection': '合成页面'}
        self.cfg = {}
        self.engine = SimpleNamespace(conn=object())
        self.stack.enter_context(patch.object(observe, '_config', side_effect=lambda: deepcopy(self.cfg)))
        self.stack.enter_context(patch.object(observe, 'band_words', side_effect=lambda *a: deepcopy(self.words)))
        self.stack.enter_context(patch.object(observe, 'page', side_effect=lambda *a: deepcopy(self.page)))
        self.stack.enter_context(patch.object(observe.metrics_mod, 'compute', side_effect=lambda *a, **k: deepcopy(self.metrics)))
        self.stack.enter_context(patch.object(observe.metrics_mod, 'compute_outcomes', return_value={'cohort_size': 0}))
        self.stack.enter_context(patch.object(llm, 'chat', side_effect=AssertionError('no model allowed')))

    def set_words(self, count):
        self.words = [{'lemma': 'fixtureword' + str(i), 'gloss': '合成释义', 's_value': .1}
                      for i in range(count)]

    def assemble(self, params):
        estimate = observe.estimate_tokens
        calls = []
        limit = max(6, len(self.words).bit_length() + 3)

        def bounded(context):
            calls.append(len(context.get('words') or []))
            self.assertLessEqual(len(calls), limit, 'context trim failed to make bounded progress')
            return estimate(context)

        original = deepcopy(self.words)
        with patch.object(observe, 'estimate_tokens', side_effect=bounded):
            result = observe.assemble(self.engine, params)
        self.assertEqual(self.words, original)
        self.assertEqual(result['budget_tokens']['estimate'], estimate({
            k: v for k, v in result.items() if k != 'budget_tokens'}))
        self.assertEqual(set(result['budget_tokens']), {'estimate', 'cap', 'trimmed', 'policy'})
        return result

    def test_single_word_over_budget_terminates(self):
        result = self.assemble({'include': ['words'], 'max_tokens': 1})
        self.assertEqual(result['words'], [])
        self.assertEqual(result['budget_tokens']['trimmed'], ['words:0'])
        self.assertGreater(result['budget_tokens']['estimate'], 1)

    def test_multiple_words_over_budget_terminate(self):
        for count in (2, 3, 8, 40, 80):
            with self.subTest(count=count):
                self.set_words(count)
                result = self.assemble({'include': ['words'], 'max_tokens': 1})
                self.assertEqual(result['words'], [])
                trimmed = result['budget_tokens']['trimmed']
                self.assertLessEqual(len(trimmed), count.bit_length())
                self.assertEqual(trimmed[-1], 'words:0')

    def test_exact_boundary_retains_word(self):
        cap = observe.estimate_tokens({'words': self.words})
        result = self.assemble({'include': ['words'], 'max_tokens': cap})
        self.assertEqual(result['words'], self.words)
        self.assertEqual(result['budget_tokens']['trimmed'], [])

    def test_normal_eight_words(self):
        self.set_words(8)
        result = self.assemble({'include': ['words', 'metrics'], 'top_n': 8, 'max_tokens': 200000})
        self.assertEqual(result['words'], self.words)
        self.assertEqual(result['metrics'], self.metrics)
        self.assertEqual(result['budget_tokens']['trimmed'], [])

    def test_partial_trim_to_one(self):
        self.set_words(8)
        cap = observe.estimate_tokens({'words': self.words[:1]})
        result = self.assemble({'include': ['words'], 'max_tokens': cap})
        self.assertEqual(result['words'], self.words[:1])
        self.assertEqual(result['budget_tokens']['trimmed'], ['words:4', 'words:2', 'words:1'])

    def test_partial_trim_to_two(self):
        self.set_words(8)
        cap = observe.estimate_tokens({'words': self.words[:2]})
        result = self.assemble({'include': ['words'], 'max_tokens': cap})
        self.assertEqual(result['words'], self.words[:2])
        self.assertEqual(result['budget_tokens']['trimmed'], ['words:4', 'words:2'])

    def test_empty_words(self):
        self.words = []
        result = self.assemble({'include': ['words'], 'max_tokens': 1})
        self.assertEqual(result['words'], [])
        self.assertEqual(result['budget_tokens']['trimmed'], [])
        self.assertGreater(result['budget_tokens']['estimate'], 1)

    def test_no_words_section(self):
        result = self.assemble({'include': ['page'], 'max_tokens': 1})
        self.assertNotIn('words', result)
        self.assertEqual(result['page'], self.page)
        self.assertEqual(result['budget_tokens']['trimmed'], [])

    def test_fixed_page_larger_than_budget_is_preserved(self):
        self.page = {'selection': '合成固定资料' * 100}
        result = self.assemble({'include': ['words', 'page'], 'max_tokens': 1})
        self.assertEqual(result['words'], [])
        self.assertEqual(result['page'], self.page)
        self.assertGreater(result['budget_tokens']['estimate'], result['budget_tokens']['cap'])

    def test_fixed_memory_is_preserved(self):
        from pae_core import memory
        facts = [{'kind': 'goal', 'text': '合成学习目标' * 100, 'confidence': .5, 'evidence': ['fixture']}]
        self.engine.conn = object()
        with patch.object(memory, 'active_facts', return_value=facts), patch.object(memory, 'recent', return_value=[]):
            result = self.assemble({'include': ['words', 'memory'], 'max_tokens': 1})
        self.assertEqual(result['words'], [])
        self.assertEqual(result['memory']['facts'], facts)
        self.assertGreater(result['budget_tokens']['estimate'], 1)

    def test_metrics_detail_trim_remains_after_words(self):
        for with_detail in (True, False):
            with self.subTest(with_detail=with_detail):
                self.metrics = {'annotation_exposure': 3}
                if with_detail:
                    self.metrics['top_ignored_words'] = ['synthetic']
                result = self.assemble({'include': ['words', 'metrics'], 'max_tokens': 1})
                self.assertEqual(result['words'], [])
                self.assertEqual(result['metrics'], {'annotation_exposure': 3})
                self.assertEqual(result['budget_tokens']['trimmed'], ['words:0', 'metrics.top_ignored_words'])

    def test_zero_budget_uses_config_default(self):
        self.cfg = {'context_max_tokens': 12345}
        result = self.assemble({'include': ['words'], 'max_tokens': 0})
        self.assertEqual(result['budget_tokens']['cap'], 12345)
        self.assertEqual(result['words'], self.words)

    def test_negative_budget_preserves_input_semantics_and_terminates(self):
        result = self.assemble({'include': ['words'], 'max_tokens': -1})
        self.assertEqual(result['budget_tokens']['cap'], -1)
        self.assertEqual(result['words'], [])

    def test_default_budget(self):
        result = self.assemble({'include': ['words']})
        self.assertEqual(result['budget_tokens']['cap'], 200000)
        self.assertEqual(result['words'], self.words)

    def test_chinese_estimate_is_not_final_serialized_object_count(self):
        result = self.assemble({'include': ['page'], 'max_tokens': 1})
        self.assertEqual(result['budget_tokens']['estimate'], observe.estimate_tokens({'page': self.page}))
        self.assertGreater(observe.estimate_tokens(result), result['budget_tokens']['estimate'])


class ContextBudgetIntegrationTests(unittest.IsolatedAsyncioTestCase):
    # Reuse only the isolated fixture setup and ASGI transport, not its tests.
    asyncSetUp = fixture.HttpWriteSourceTests.asyncSetUp
    call = fixture.HttpWriteSourceTests.call

    def guard(self):
        original = observe.estimate_tokens
        calls = []

        def bounded(context):
            calls.append(1)
            self.assertLessEqual(len(calls), 8, 'context trim failed to make bounded progress')
            return original(context)

        return patch.object(observe, 'estimate_tokens', side_effect=bounded)

    def seed(self):
        self.eng.annotate(fixture.WORD, 'synthetic-context-page')
        self.assertEqual(observe.band_words(self.eng, 8)[0]['lemma'], fixture.WORD)

    def learning_snapshot(self):
        return (repr(self.eng.states), repr(self.eng.contacts),
                list(self.eng.conn.execute('SELECT * FROM events ORDER BY rowid')))

    async def test_capability_tiny_budget_returns_without_learning_changes(self):
        self.seed()
        before = self.learning_snapshot()
        audits = self.eng.conn.execute('SELECT COUNT(*) FROM cap_calls').fetchone()[0]
        with self.guard(), patch.object(observe, '_config', return_value={}), \
                patch.object(llm, 'chat', side_effect=AssertionError('no model allowed')):
            status, body = capsurface.handle('pae.context', {'include': ['words'], 'max_tokens': 1})
        self.assertEqual(status, 200, body)
        self.assertTrue(body['ok'])
        self.assertEqual(body['result']['words'], [])
        self.assertEqual(self.learning_snapshot(), before)
        self.assertEqual(self.eng.conn.execute('SELECT COUNT(*) FROM cap_calls').fetchone()[0], audits + 1)

    async def test_asgi_tiny_budget_returns_original_envelope(self):
        self.seed()
        before = self.learning_snapshot()
        with self.guard(), patch.object(observe, '_config', return_value={}), \
                patch.object(llm, 'chat', side_effect=AssertionError('no model allowed')):
            status, body = await self.call('/v1/cap/pae.context', {'include': ['words'], 'max_tokens': 1})
        self.assertEqual(status, 200, body)
        self.assertEqual(set(body), {'ok', 'actor', 'capability', 'result'})
        self.assertEqual(body['capability'], 'pae.context')
        self.assertEqual(body['result']['words'], [])
        self.assertEqual(self.learning_snapshot(), before)

    async def test_asgi_normal_sidebar_context_preserves_word_and_metrics(self):
        self.seed()
        before = self.learning_snapshot()
        with self.guard(), patch.object(observe, '_config', return_value={}), \
                patch.object(llm, 'chat', side_effect=AssertionError('no model allowed')):
            status, body = await self.call('/v1/cap/pae.context', {'include': ['words', 'metrics'], 'top_n': 8})
        self.assertEqual(status, 200, body)
        result = body['result']
        self.assertEqual(result['words'][0]['lemma'], fixture.WORD)
        self.assertIsInstance(result['metrics'], dict)
        self.assertEqual(result['budget_tokens']['trimmed'], [])
        self.assertEqual(self.learning_snapshot(), before)


if __name__ == '__main__':
    unittest.main(verbosity=2)
