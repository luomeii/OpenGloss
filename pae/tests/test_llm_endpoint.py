"""Shared chat address and permission regressions using synthetic I/O only."""
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
from urllib.error import HTTPError


class EndpointTests(unittest.TestCase):
    def setUp(self):
        modules = patch.dict(sys.modules)
        modules.start()
        self.addCleanup(modules.stop)
        root = Path(__file__).resolve().parents[1] / 'pae_core'
        pkg = types.ModuleType('_endpoint_fixture')
        pkg.__path__ = [str(root)]
        sys.modules[pkg.__name__] = pkg

        def load(name):
            spec = importlib.util.spec_from_file_location(pkg.__name__ + '.' + name, root / (name + '.py'))
            mod = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = mod
            spec.loader.exec_module(mod)
            return mod

        self.controls = load('user_controls')
        self.state = {**self.controls.DEFAULTS, 'ai_enabled': True}
        self.controls.load = lambda: dict(self.state)
        self.llm = load('llm')
        self.cfg = {'base': 'https://config.example.invalid', 'model': 'synthetic-model',
                    'key': 'synthetic-placeholder', 'thinking': 'disabled'}
        self.llm.CONFIG = types.SimpleNamespace(read_text=lambda **kw: json.dumps({'llm': self.cfg}))
        env = patch.dict(self.llm.os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.requests = []
        opener = patch.object(self.llm.urllib.request, 'build_opener',
                              return_value=types.SimpleNamespace(open=self.open))
        opener.start()
        self.addCleanup(opener.stop)

    def open(self, request, **kwargs):
        self.requests.append((request, kwargs))
        class Reply:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self):
                return b'{"choices":[{"message":{"content":" reply ","reasoning_content":" reason "}}],"usage":{"total_tokens":2},"model":"synthetic-response"}'
        return Reply()

    def check_address(self, base, expected):
        self.state['base'] = base
        before = len(self.requests)
        messages = [{'role': 'user', 'content': 'synthetic question'}]
        result = self.llm.chat(messages, temperature=0.4, timeout=17, max_tokens=23, thinking=False)
        self.assertEqual(len(self.requests), before + 1)
        req, kw = self.requests[-1]
        self.assertEqual(req.full_url, expected)
        self.assertEqual(json.loads(req.data), {'model': 'synthetic-model', 'messages': messages,
                         'temperature': 0.4, 'max_tokens': 23, 'thinking': {'type': 'disabled'}})
        self.assertEqual(req.get_header('Authorization'), 'Bearer synthetic-placeholder')
        self.assertEqual(req.get_header('Content-type'), 'application/json')
        self.assertEqual(kw, {'timeout': 17})
        self.assertEqual(result, {'content': 'reply', 'reasoning': 'reason',
                                 'usage': {'total_tokens': 2}, 'model': 'synthetic-response'})

    def test_v1_suffix(self):
        for base in ['https://provider.example.invalid/v1', 'https://provider.example.invalid/v1/',
                     'https://provider.example.invalid/proxy/v1', 'http://[::1]:8500/proxy/v1',
                     'https://provider.example.invalid/proxy%2Fsegment/v1']:
            with self.subTest(base=base):
                self.check_address(base, base.rstrip('/') + '/chat/completions')

    def test_complete_endpoint(self):
        for base in ['https://provider.example.invalid/v1/chat/completions',
                     'https://provider.example.invalid/team/proxy/v1/chat/completions/',
                     'https://provider.example.invalid/api/paas/v4/chat/completions',
                     'https://provider.example.invalid/v1/v1/chat/completions']:
            with self.subTest(base=base):
                self.check_address(base, base.rstrip('/'))

    def test_other_paths_retain_old_construction(self):
        for base in ['https://provider.example.invalid', 'https://provider.example.invalid/',
                     'https://provider.example.invalid/proxy', 'https://provider.example.invalid/V1',
                     'https://provider.example.invalid/v2', 'https://provider.example.invalid/%76%31',
                     'https://provider.example.invalid/v1/Chat/Completions']:
            with self.subTest(base=base):
                self.check_address(base, base.rstrip('/') + '/v1/chat/completions')

    def test_legacy_invalid_configuration_is_not_cleaned(self):
        for base in ['ftp://provider.example.invalid/v1', 'https://provider.example.invalid/v1?q=synthetic',
                     'https://provider.example.invalid/v1#synthetic',
                     'https://synthetic:placeholder@provider.example.invalid/v1',
                     'https://provider.example.invalid:99999/v1']:
            with self.subTest(base=base):
                with self.assertRaises(ValueError): self.controls._validate({'base': base})
                self.check_address(base, base.rstrip('/') + '/v1/chat/completions')

    def test_provider_precedence_and_saved_value_unchanged(self):
        for user, env, source, expected in [('', '', 'config', 'https://config.example.invalid'),
                ('https://user.example.invalid/v1', '', 'user', 'https://user.example.invalid/v1'),
                ('https://user.example.invalid/v1', 'https://env.example.invalid/v1/chat/completions',
                 'environment', 'https://env.example.invalid/v1/chat/completions')]:
            with self.subTest(source=source), patch.dict(self.llm.os.environ, {'PAE_LLM_BASE': env}):
                self.state['base'] = user
                before = dict(self.state)
                self.assertEqual(self.llm.load_provider()['base'], expected)
                self.assertEqual(self.llm.public_provider()['base_source'], source)
                self.llm.chat([])
                target = {'config': expected + '/v1/chat/completions',
                          'user': expected + '/chat/completions', 'environment': expected}[source]
                self.assertEqual(self.requests[-1][0].full_url, target)
                self.assertEqual(self.state, before)

    def test_existing_purpose_permissions(self):
        cases = [(False, False, 'manual', None, False), (True, False, 'manual', None, True),
                 (True, False, 'shadow', None, False), (True, False, 'scheduled', None, False),
                 (True, False, 'proactive', None, False), (True, False, 'manual', 'proactive', False),
                 (True, True, 'shadow', None, True), (True, True, 'scheduled', None, True),
                 (True, True, 'manual', 'proactive', True), (False, True, 'scheduled', None, False)]
        self.state['base'] = 'https://provider.example.invalid/v1'
        for master, background, context, explicit, allowed in cases:
            with self.subTest(case=(master, background, context, explicit)):
                self.state.update(ai_enabled=master, shadow_ai_enabled=background,
                                  scheduled_ai_enabled=background, proactive_enabled=background,
                                  proactive_ai_enabled=background)
                before = len(self.requests)
                with self.llm.call_purpose(context):
                    if allowed: self.llm.chat([], purpose=explicit)
                    else:
                        with self.assertRaisesRegex(RuntimeError, 'AI call disabled'):
                            self.llm.chat([], purpose=explicit)
                self.assertEqual(len(self.requests) - before, int(allowed))
                self.assertEqual(self.llm.current_purpose(), 'manual')

    def test_missing_key_and_late_disable_make_no_requests(self):
        self.state.update(key_overridden=True, key='')
        with self.assertRaisesRegex(RuntimeError, 'no LLM key'): self.llm.chat([])
        self.state.update(key_overridden=False)
        with patch.object(self.controls, 'allowed', side_effect=[True, False]) as gate:
            with self.assertRaisesRegex(RuntimeError, 'AI call disabled'): self.llm.chat([])
            self.assertEqual(gate.call_count, 2)
        self.assertEqual(self.requests, [])

    def test_failure_is_not_retried(self):
        self.state['base'] = 'https://provider.example.invalid/v1'
        with patch.object(self.llm.urllib.request, 'build_opener') as builder:
            builder.return_value.open.side_effect = HTTPError(
                'https://provider.example.invalid/v1/chat/completions', 404, 'synthetic', {}, None)
            opener = builder.return_value.open
            with self.assertRaises(HTTPError): self.llm.chat([])
            self.assertEqual(opener.call_count, 1)


if __name__ == '__main__':
    # Guard the isolated test process against accidental real services or children.
    def no_external(event, args):
        if event in ('socket.connect', 'socket.getaddrinfo', 'subprocess.Popen'):
            raise AssertionError('Synthetic tests cannot access services or launch processes')
    sys.addaudithook(no_external)
    unittest.main()
