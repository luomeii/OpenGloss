"""AI setting save regressions with synthetic files; no service or provider calls."""
import ast
import importlib.util
import json
from pathlib import Path
import socket
import tempfile
import types
import unittest
from unittest.mock import patch


PAE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('manage_save_controls', PAE / 'pae_core/user_controls.py')
controls = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controls)


class JsonResponse:
    def __init__(self, content, status_code=200, headers=None):
        self.content, self.status_code, self.headers = content, status_code, headers


def handler():
    # Importing the HTTP application is unnecessary for this unit. Execute its
    # actual endpoint/helper bodies, leaving ASGI and source checks to HTTP tests.
    tree = ast.parse((PAE / 'pae_core/api.py').read_text(encoding='utf-8'))
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)
                 and n.name in {'manage_ai', '_manage_ai_save_error'}]
    for node in functions:
        node.decorator_list = []
    provider = types.SimpleNamespace(public_provider=lambda: {'key_configured': bool(controls.load()['key'])})
    scope = {'Request': object, 'Body': lambda *args: None, 'JSONResponse': JsonResponse,
             '_local_write': lambda request: request is True, '_MANAGE_HEADERS': {'Cache-Control': 'no-store'},
             'user_controls': controls, 'llm_mod': provider}
    exec(compile(ast.Module(body=functions, type_ignores=[]), '<management endpoint>', 'exec'), scope)
    return scope['manage_ai']


class ManageAiSaveTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='opengloss-manage-save-')
        self.addCleanup(self.directory.cleanup)
        self.file = Path(self.directory.name) / 'controls.json'
        self.addCleanup(patch.stopall)
        patch.object(controls, 'path', return_value=self.file).start()
        patch.object(socket.socket, 'connect', side_effect=AssertionError('no network')).start()
        self.save = handler()

    def submit(self, body):
        return self.save(True, body)

    def test_connection_only_enables_manual_without_changing_background(self):
        controls.save({'proactive_gap_minutes': 60, 'shadow_ai_enabled': True})
        result = self.submit({'base': 'https://example.invalid', 'model': 'synthetic-model',
                              'key': 'synthetic-not-a-real-key', 'ai_enabled': True})
        self.assertEqual(result.status_code, 200)
        self.assertTrue(controls.allowed('manual'))
        self.assertTrue(controls.allowed('shadow'))  # Existing opt-in is retained.
        self.assertFalse(controls.allowed('proactive'))
        self.assertFalse(controls.allowed('scheduled'))
        self.assertEqual(controls.load()['proactive_gap_minutes'], 60)
        self.assertNotIn('synthetic-not-a-real-key', json.dumps(result.content))

    def test_invalid_gap_reports_field_and_preserves_existing_bytes(self):
        controls.save({'ai_enabled': True, 'proactive_gap_minutes': 60})
        before = self.file.read_bytes()
        for value in (None, 0, 15, 29, 30.5, 1441, '60', True):
            with self.subTest(value=value):
                result = self.submit({'proactive_gap_minutes': value})
                self.assertEqual(result.status_code, 400)
                self.assertEqual(result.content.get('field'), 'proactive_gap_minutes')
                self.assertIn('30–1440', result.content.get('detail', ''))
                self.assertEqual(self.file.read_bytes(), before)

    def test_gap_boundaries_stay_valid_and_background_save_keeps_provider(self):
        controls.save({'base': 'https://example.invalid', 'model': 'synthetic-model',
                       'key': 'synthetic-not-a-real-key', 'ai_enabled': True})
        for value in (30, 1440):
            result = self.submit({'proactive_gap_minutes': value, 'shadow_ai_enabled': False,
                                  'proactive_enabled': False, 'proactive_ai_enabled': False,
                                  'scheduled_ai_enabled': False})
            self.assertEqual(result.status_code, 200)
            self.assertTrue(controls.allowed('manual'))
            for purpose in ('shadow', 'proactive', 'scheduled'):
                self.assertFalse(controls.allowed(purpose))
            self.assertEqual(controls.load()['key'], 'synthetic-not-a-real-key')

    def test_invalid_fields_are_safe_and_actionable(self):
        for body, field in (({'base': 'https://example.invalid:private-port'}, 'base'),
                            ({'base': 'https://synthetic:private@[::1]'}, 'base'),
                            ({'model': ' '}, 'model'), ({'key': 123}, 'key'),
                            ({'ai_enabled': 'yes'}, 'ai_enabled')):
            with self.subTest(field=field):
                result = self.submit(body)
                self.assertEqual(result.status_code, 400)
                self.assertEqual(result.content.get('field'), field)
                self.assertTrue(result.content.get('detail'))
                self.assertNotIn('private', json.dumps(result.content))
        self.assertFalse(self.file.exists())

    def test_bad_patch_and_key_conflict_remain_rejected(self):
        for body in ({}, {'unexpected': 'synthetic'}, {'clear_key': True, 'key': 'synthetic'}):
            self.assertEqual(self.submit(body).status_code, 400)
        self.assertFalse(self.file.exists())

    def test_replace_failure_is_distinct_safe_and_does_not_claim_saved(self):
        controls.save({'ai_enabled': True, 'model': 'synthetic-before'})
        before = self.file.read_bytes()
        with patch.object(controls.os, 'replace', side_effect=OSError('synthetic private path and secret')):
            result = self.submit({'model': 'synthetic-after'})
        self.assertEqual(result.status_code, 400)
        self.assertEqual(result.content.get('reason'), 'settings_unwritable')
        self.assertIn('未保存', result.content.get('detail', ''))
        self.assertNotIn('private', json.dumps(result.content))
        self.assertEqual(self.file.read_bytes(), before)
        self.assertEqual(list(self.file.parent.glob('.controls-*')), [])

    def test_damaged_settings_remain_unmodified_and_fail_closed(self):
        self.file.write_text('{ synthetic damaged JSON', encoding='utf-8')
        before = self.file.read_bytes()
        result = self.submit({'ai_enabled': True})
        self.assertEqual(result.status_code, 400)
        self.assertEqual(result.content.get('reason'), 'settings_unreadable')
        self.assertIn('未保存', result.content.get('detail', ''))
        self.assertFalse(controls.allowed('manual'))
        self.assertEqual(self.file.read_bytes(), before)

    def test_key_is_retained_until_explicit_clear(self):
        self.submit({'key': 'synthetic-not-a-real-key'})
        self.submit({'model': 'synthetic-model'})
        self.assertTrue(controls.public_state()['key_configured'])
        self.assertEqual(self.submit({'clear_key': True}).status_code, 200)
        self.assertFalse(controls.public_state()['key_configured'])

    def test_origin_rejection_still_precedes_validation_and_write(self):
        result = self.save(False, {'ai_enabled': True})
        self.assertEqual(result.status_code, 403)
        self.assertEqual(result.content['error'], 'local_origin_required')
        self.assertFalse(self.file.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
