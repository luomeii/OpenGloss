from pathlib import Path
import io, json, os, sys, tempfile, unittest, urllib.error
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from distribution import bootstrap, network, package


class InstallNetworkContracts(unittest.TestCase):
    def test_only_locked_public_ca_pem_is_a_payload(self):
        self.assertEqual(str(package.payload_path('runtime/site-packages/certifi/cacert.pem')),
                         'runtime/site-packages/certifi/cacert.pem')
        for path in ('runtime/site-packages/certifi/private.pem',
                     'runtime/site-packages/other/cacert.pem',
                     'app/pae/private.pem'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                package.payload_path(path)

    def test_https_uses_explicit_certifi_context_without_proxy(self):
        handlers = []
        class Opener:
            def open(self, request, timeout):
                return io.BytesIO(b'{}')
        def build(*items):
            handlers.extend(items)
            return Opener()
        with patch.object(network.urllib.request, 'build_opener', side_effect=build), \
             patch.object(network.ssl, 'create_default_context') as context, \
             patch.object(network.certifi, 'where', return_value='synthetic-ca.pem'):
            with network.open_public(package.API) as response:
                response.read()
        context.assert_called_once_with(cafile='synthetic-ca.pem')
        self.assertTrue(any(isinstance(item, network.urllib.request.HTTPSHandler)
                            for item in handlers))
        self.assertTrue(any(isinstance(item, network.urllib.request.ProxyHandler)
                            and item.proxies == {} for item in handlers))

    def test_latest_404_is_a_fixed_no_stable_release_category_and_closes_response(self):
        response = urllib.error.HTTPError(package.API, 404, 'secret-local-detail', {}, io.BytesIO(b'no release'))
        class Opener:
            def open(self, request, timeout):
                raise response
        with patch.object(network.urllib.request, 'build_opener', return_value=Opener()):
            with self.assertRaises(network.ReleaseUnavailable) as raised:
                network.latest('0.5.0')
        self.assertTrue(response.fp is None or response.fp.closed)
        self.assertNotIn('secret-local-detail', str(raised.exception))
        self.assertNotIn(package.API, str(raised.exception))

    def test_engine_environment_ignores_legacy_token_and_uses_own_port(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); data = root / 'data'; data.mkdir()
            (data / 'http-auth.json').write_text(json.dumps({'token': 'a' * 43}), encoding='utf-8')
            env = bootstrap.engine_environment(root / 'versions/0.5.0', data, engine_port=54975)
            self.assertNotIn('PAE_HTTP_TOKEN',env)
            self.assertEqual(env['PAE_ENGINE_PORT'], '54975')
            self.assertNotIn('http-auth.json', str(env))

    def test_snapshot_and_restore_include_both_runtime_secrets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); data = root / 'data'; data.mkdir()
            for name in ('privacy-key.json', 'http-auth.json'):
                (data / name).write_text('{"token":"before"}', encoding='utf-8')
            backup = package.snapshot_data(root)
            for name in ('privacy-key.json', 'http-auth.json'):
                (data / name).write_text('{"token":"after"}', encoding='utf-8')
            package.restore_data(root, backup)
            for name in ('privacy-key.json', 'http-auth.json'):
                self.assertEqual(json.loads((data / name).read_text())['token'], 'before')


if __name__ == '__main__':
    unittest.main()
