"""AI redirects through real urllib dispatch with memory-only HTTP responses."""
from contextlib import ExitStack
from email.message import Message
import importlib.util
import io
import json
from pathlib import Path
import sys
import types
import unittest
import urllib.request
from urllib.error import HTTPError
from urllib.response import addinfourl
from unittest.mock import patch

CASES = [{'name': 'direct_200', 'expected': 'ok'}, {'name': 'same_origin_301', 'code': 301, 'target': 'https://provider.example.invalid/final', 'expected': 'ok'}, {'name': 'same_origin_302', 'code': 302, 'target': 'https://provider.example.invalid/final', 'expected': 'ok'}, {'name': 'same_origin_303', 'code': 303, 'target': 'https://provider.example.invalid/final', 'expected': 'ok'}, {'name': 'same_origin_post_307', 'code': 307, 'target': 'https://provider.example.invalid/final', 'expected': 'HTTPError'}, {'name': 'same_origin_post_308', 'code': 308, 'target': 'https://provider.example.invalid/final', 'expected': 'HTTPError'}, {'name': 'same_origin_case_default_port', 'code': 302, 'target': 'https://PROVIDER.example.invalid:443/final', 'expected': 'ok'}, {'name': 'cross_origin_301', 'code': 301, 'target': 'https://other.example.invalid/final', 'expected': 'blocked'}, {'name': 'cross_origin_302', 'code': 302, 'target': 'https://other.example.invalid/final', 'expected': 'blocked'}, {'name': 'cross_origin_303', 'code': 303, 'target': 'https://other.example.invalid/final', 'expected': 'blocked'}, {'name': 'cross_origin_post_307', 'code': 307, 'target': 'https://other.example.invalid/final', 'expected': 'blocked'}, {'name': 'cross_origin_post_308', 'code': 308, 'target': 'https://other.example.invalid/final', 'expected': 'blocked'}, {'name': 'https_downgrade', 'code': 302, 'target': 'http://provider.example.invalid/final', 'expected': 'blocked'}, {'name': 'different_port', 'code': 302, 'target': 'https://provider.example.invalid:444/final', 'expected': 'blocked'}, {'name': 'http_to_https', 'base': 'http://provider.example.invalid', 'code': 302, 'target': 'https://provider.example.invalid/final', 'expected': 'blocked'}, {'name': 'http_same_origin', 'base': 'http://provider.example.invalid', 'code': 302, 'target': 'http://provider.example.invalid:80/final', 'expected': 'ok'}, {'name': 'ipv6_same_origin', 'base': 'http://[::1]:8500', 'code': 302, 'target': 'http://[::1]:8500/final', 'expected': 'ok'}, {'name': 'userinfo_target', 'code': 302, 'target': 'https://synthetic@provider.example.invalid/final', 'expected': 'blocked'}, {'name': 'bad_port_target', 'code': 302, 'target': 'https://provider.example.invalid:bad/final', 'expected': 'blocked'}, {'name': 'ai_disabled', 'enabled': False, 'expected': 'permission'}, {'name': 'no_key', 'key': '', 'expected': 'no_key'}]
MARKER = 'SYNTHETIC-REDIRECT-PLACEHOLDER'


class RedirectTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(sys.modules))
        self.stack.enter_context(patch.dict('os.environ', {}, clear=True))
        self.stack.enter_context(patch.object(urllib.request, 'getproxies', return_value={}))
        root = Path(__file__).resolve().parents[1] / 'pae_core'
        pkg = types.ModuleType('_redirect_fixture')
        pkg.__path__ = [str(root)]
        sys.modules[pkg.__name__] = pkg
        for name in ('user_controls', 'llm'):
            spec = importlib.util.spec_from_file_location(pkg.__name__ + '.' + name, root / (name + '.py'))
            mod = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = mod
            spec.loader.exec_module(mod)
            setattr(self, name, mod)

    def check_case(self, case):
        state = {**self.user_controls.DEFAULTS, 'ai_enabled': case.get('enabled', True)}
        self.user_controls.load = lambda: dict(state)
        provider = {'base': case.get('base', 'https://provider.example.invalid'),
                    'model': 'synthetic-model', 'key': case.get('key', MARKER), 'thinking': 'disabled'}
        self.llm.CONFIG = types.SimpleNamespace(read_text=lambda **kw: json.dumps({'llm': provider}))
        requests = []
        class MemoryTransport(urllib.request.HTTPHandler, urllib.request.HTTPSHandler):
            def http_open(inner, request):
                requests.append(request)
                headers = Message()
                if len(requests) == 1 and 'target' in case:
                    headers['Location'] = case['target']
                    response = addinfourl(io.BytesIO(b''), headers, request.full_url, code=case['code'])
                    response.msg = 'Synthetic redirect'
                else:
                    response = addinfourl(io.BytesIO(b'{"choices":[{"message":{"content":" synthetic reply "}}]}'),
                                         headers, request.full_url, code=200)
                    response.msg = 'OK'
                return response
            https_open = http_open
        builder = urllib.request.build_opener
        default = builder(MemoryTransport())
        def local_builder(*handlers):
            return builder(*handlers, MemoryTransport())
        with patch.object(urllib.request, '_opener', default), patch.object(urllib.request, 'build_opener', local_builder):
            expected = case['expected']
            if expected == 'ok':
                result = self.llm.chat([{'role': 'user', 'content': 'synthetic question'}], timeout=17)
                self.assertEqual(result['content'], 'synthetic reply')
                self.assertEqual(len(requests), 2 if 'target' in case else 1)
                for request in requests:
                    self.assertEqual(request.get_header('Authorization'), 'Bearer ' + MARKER)
                    self.assertEqual(request.timeout, 17)
                self.assertEqual(requests[0].get_method(), 'POST')
                if 'target' in case:
                    self.assertEqual(requests[1].get_method(), 'GET')
            else:
                error = HTTPError if expected == 'HTTPError' else RuntimeError
                message = {'blocked': 'AI endpoint redirect blocked; configure the final API URL',
                           'permission': 'AI call disabled for this action', 'no_key': 'no LLM key configured'}.get(expected)
                with self.assertRaises(error) as caught:
                    self.llm.chat([], timeout=17)
                if message:
                    self.assertEqual(str(caught.exception), message)
                self.assertEqual(len(requests), 0 if expected in ('permission', 'no_key') else 1)
            self.assertIs(urllib.request._opener, default)


def make_case(case):
    def run(self):
        self.check_case(case)
    return run


for case in CASES:
    setattr(RedirectTests, 'test_' + case['name'], make_case(case))

if __name__ == '__main__':
    def no_external(event, args):
        if event in ('socket.connect', 'socket.bind', 'socket.getaddrinfo', 'subprocess.Popen'):
            raise AssertionError('Synthetic redirect tests cannot use real services')
    sys.addaudithook(no_external)
    unittest.main(verbosity=2)
