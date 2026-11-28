"""Real chat dispatch with in-memory SQL and synthetic model/memory writers.

No engine, settings, user database, provider or network is loaded. These tests
check writer invocations, not durable memory storage or provider compatibility.
Run: python -I -B pae/tests/test_chat_feedback.py
"""
import importlib.util
from pathlib import Path
import sqlite3
import sys
import types
import unittest
from unittest.mock import patch


class ChatFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.modules = patch.dict(sys.modules)
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.prefix = '_chat_feedback_fixture'
        self.root = Path(__file__).resolve().parents[1] / 'pae_core'
        self.pkg = self.module('')
        self.pkg.__path__ = [str(self.root)]
        self.conn = sqlite3.connect(':memory:')
        self.addCleanup(self.conn.close)
        self.conn.executescript(
            'CREATE TABLE cap_calls(capability TEXT,ts INTEGER,ok INTEGER);'
            'CREATE TABLE facts(source_actor TEXT,created_at INTEGER);')
        engine = self.module('engine')
        engine.get_engine = lambda: types.SimpleNamespace(conn=self.conn)
        engine.LearningWritePaused = type('LearningWritePaused', (RuntimeError,), {})
        keys = self.module('keys')
        keys.resolve = lambda key: ('local', ['read', 'write'])
        persona = self.module('persona')
        persona.load_params = lambda: {}
        persona.render = lambda *args, **kwargs: 'synthetic persona'
        llm = self.module('llm')
        self.model_reply = ''
        self.model_calls = []

        def chat(messages, **kwargs):
            self.model_calls.append((messages, kwargs))
            return {'content': self.model_reply, 'model': 'synthetic-model'}

        llm.chat = chat
        memory = self.module('memory')
        memory.recent = lambda *args: []
        self.memory_calls = []
        self.memory_ok = True
        self.memory_body = {'fact_id': 'synthetic-fact'}

        def remember(*args, **kwargs):
            self.memory_calls.append((args, kwargs))
            return self.memory_ok, self.memory_body

        memory.remember = remember
        spec = importlib.util.spec_from_file_location(
            self.prefix + '.capsurface', self.root / 'capsurface.py')
        self.caps = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.caps
        spec.loader.exec_module(self.caps)
        self.caps.audit = lambda *args, **kwargs: None
        self.caps._config = lambda: {'chat_history_turns': 64}
        self.turns = []
        self.caps._remember_turns = lambda conn, user, agent: self.turns.append((conn, user, agent))

    def module(self, name):
        fullname = self.prefix + ('.' + name if name else '')
        module = types.ModuleType(fullname)
        sys.modules[fullname] = module
        return module

    def say(self, stimulus='你好'):
        status, body = self.caps.handle('pae.persona.say', {
            'stimulus': stimulus, 'context_block': 'synthetic context'}, actor_hint='local')
        self.assertEqual(status, 200)
        self.assertIs(body['ok'], True)
        self.assertEqual(len(self.model_calls), 1)
        self.assertEqual(self.turns, [(self.conn, stimulus, body['result']['reply'])])
        return body['result']

    def assert_no_confirmation(self, result):
        self.assertEqual(result['reply'], '这次没有收到可显示的回复，请再试一次。')

    def test_empty_without_memory_intent_is_not_a_memory_confirmation(self):
        result = self.say()
        self.assert_no_confirmation(result)
        self.assertIsNone(result['remembered'])
        self.assertEqual(self.memory_calls, [])

    def test_whitespace_without_memory_intent_is_not_a_confirmation(self):
        self.model_reply = ' \n\t'
        self.assert_no_confirmation(self.say())
        self.assertEqual(self.memory_calls, [])

    def test_successful_intent_keeps_confirmation_and_original_writer_identity(self):
        stimulus = '记住我喜欢合成示例'
        result = self.say(stimulus)
        self.assertEqual(result['reply'], '记下了。')
        self.assertIs(result['remembered']['ok'], True)
        self.assertEqual(len(self.memory_calls), 1)
        args, kwargs = self.memory_calls[0]
        self.assertEqual(args, (self.conn, 'user', 'preference', '我喜欢合成示例',
                                [{'via': 'chat_intent', 'stimulus': stimulus}]))
        self.assertEqual(kwargs, {'confidence': 0.9})

    def test_failed_intent_does_not_confirm(self):
        self.memory_ok = False
        result = self.say('记住我喜欢合成示例')
        self.assertIs(result['remembered']['ok'], False)
        self.assertEqual(len(self.memory_calls), 1)
        self.assert_no_confirmation(result)

    def test_marker_only_success_keeps_confirmation_and_original_writer_identity(self):
        self.model_reply = '[[记住]] 喜欢合成示例'
        stimulus = '记住我喜欢合成示例'
        result = self.say(stimulus)
        self.assertEqual(result['reply'], '记下了。')
        self.assertEqual(len(self.memory_calls), 1)
        args, kwargs = self.memory_calls[0]
        self.assertEqual(args, (self.conn, 'companion', 'event', '喜欢合成示例',
                                [{'stimulus': stimulus}]))
        self.assertEqual(kwargs, {'confidence': 0.8})

    def test_marker_only_failure_does_not_confirm(self):
        self.model_reply = '【记住】 喜欢合成示例'
        self.memory_ok = False
        self.assert_no_confirmation(self.say())
        self.assertEqual(len(self.memory_calls), 1)

    def test_nonempty_reply_is_preserved(self):
        self.model_reply = '合成回答。'
        self.assertEqual(self.say()['reply'], '合成回答。')
        self.assertEqual(self.memory_calls, [])

    def test_daily_intent_limit_still_blocks_memory_write(self):
        self.conn.executemany('INSERT INTO facts VALUES (?, ?)', [('user', 2**40)] * 30)
        result = self.say('记住我喜欢合成示例')
        self.assertEqual(result['remembered']['skipped'], 'daily_intent_limit')
        self.assertEqual(self.memory_calls, [])
        self.assert_no_confirmation(result)

    def test_truthy_nonboolean_memory_receipt_is_not_success(self):
        self.memory_body = {'ok': 1}
        self.assert_no_confirmation(self.say('记住我喜欢合成示例'))
        self.assertEqual(len(self.memory_calls), 1)


if __name__ == '__main__':
    unittest.main()
