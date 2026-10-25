from pathlib import Path
import io, json, socket, sys, tempfile, threading, time, unittest, urllib.error, urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from distribution import bootstrap, package


class ManagementApiContracts(unittest.TestCase):
    def test_management_page_has_pairing_privacy_and_local_lifecycle_controls(self):
        html = (ROOT / 'distribution/manage.html').read_text(encoding='utf-8')
        script = (ROOT / 'distribution/manage.js').read_text(encoding='utf-8')
        for control in ('operation-status', 'url-storage', 'context-storage', 'shadow-model',
                        'autostart', 'export', 'open-data', 'uninstall'):
            with self.subTest(control=control): self.assertIn('id="' + control + '"', html)
        self.assertNotIn('chrome.storage.local.set', html)
        self.assertNotIn('data_directory', script)

    def test_status_and_privacy_work_directly_but_foreign_origin_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package.atomic_json(root / 'installation.json', {'product': 'OpenGloss',
                'instance_id': 'd' * 32, 'engine_port': 54975, 'manager_port': 54976})
            package.atomic_json(root / 'active.json', {'version': '0.5.0', 'previous': None})
            (root / 'data').mkdir()
            package.atomic_json(root / 'data/config.json', {'privacy': {'url_storage': 'full',
                'context_storage': True, 'shadow_model': False}})
            package.atomic_json(root / 'data/http-auth.json', {'token': 'e' * 43})
            def fake_start(self, version=None): return True
            def fake_stop(self): return None
            with patch.object(bootstrap.Supervisor, 'start', fake_start), \
                 patch.object(bootstrap.Supervisor, 'stop', fake_stop), \
                 patch.object(bootstrap.Supervisor, 'submit', lambda self, name: None):
                worker = threading.Thread(target=bootstrap._run_owned, args=(root,),
                    kwargs={'no_browser': True}, daemon=True)
                worker.start()
                session = root / 'manager-session.json'
                for _ in range(100):
                    if session.exists(): break
                    time.sleep(.02)
                self.assertTrue(session.exists())
                self.assertNotIn('token',package.read_json(session))
                base = 'http://127.0.0.1:54976'
                def request(path, method='GET', body=None, authorized=True):
                    headers = {'Host': '127.0.0.1:54976'}
                    if not authorized: headers['Origin'] = 'https://foreign.invalid'
                    if body is not None: headers['Content-Type'] = 'application/json'
                    item = urllib.request.Request(base + path, data=body, headers=headers, method=method)
                    try:
                        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(item, timeout=3) as response:
                            return response.status, json.load(response)
                    except urllib.error.HTTPError as error:
                        error.close()
                        raise
                try:
                    with self.assertRaises(urllib.error.HTTPError) as denied:
                        request('/api/status', authorized=False)
                    self.assertEqual(denied.exception.code, 403)
                    status, state = request('/api/status')
                    self.assertEqual(status, 200)
                    self.assertNotIn('engine_token',state)
                    self.assertNotIn(str(root), json.dumps(state, ensure_ascii=False))
                    payload = json.dumps({'url_storage': 'hash', 'context_storage': False,
                        'shadow_model': True}).encode()
                    status, _ = request('/api/privacy', 'POST', payload)
                    self.assertIn(status, (200, 202))
                    self.assertEqual(package.read_json(root / 'data/config.json')['privacy']['url_storage'], 'hash')
                    with self.assertRaises(urllib.error.HTTPError) as invalid:
                        request('/api/privacy', 'POST', b'{"url_storage":"invalid"}')
                    self.assertEqual(invalid.exception.code, 400)
                finally:
                    try: request('/api/stop', 'POST', b'{}')
                    except Exception: pass
                    worker.join(timeout=4)
                    self.assertFalse(worker.is_alive(), 'synthetic management server must exit')


if __name__ == '__main__':
    unittest.main()
