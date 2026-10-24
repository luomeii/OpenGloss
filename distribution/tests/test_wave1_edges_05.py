from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import ssl, sys, tempfile, threading, unittest, urllib.error
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from distribution import bootstrap, network, package


class FirstWaveEdges(unittest.TestCase):
    def test_simultaneous_token_creation_never_overwrites_first_credential(self):
        with tempfile.TemporaryDirectory() as temp:
            data = Path(temp)
            barrier = threading.Barrier(2)
            original = package.atomic_json
            def competing_write(path, value):
                if Path(path).name == 'http-auth.json': barrier.wait(timeout=2)
                return original(path, value)
            with patch.object(package, 'atomic_json', side_effect=competing_write), \
                 patch.object(bootstrap.secrets, 'token_urlsafe', side_effect=['a' * 43, 'b' * 43]):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    values = list(pool.map(lambda _: bootstrap.engine_token(data), range(2)))
            final = package.read_json(data / 'http-auth.json')['token']
            self.assertEqual(values, [final, final])

    def test_wrapped_certificate_failure_has_distinct_fixed_category(self):
        class Opener:
            def open(self, request, timeout):
                raise urllib.error.URLError(ssl.SSLCertVerificationError('synthetic-private-path'))
        with patch.object(network.urllib.request, 'build_opener', return_value=Opener()):
            with self.assertRaises(network.PublicNetworkError) as raised:
                network.latest('0.5.0')
        self.assertIn('证书', str(raised.exception))
        self.assertNotIn('synthetic-private-path', str(raised.exception))


if __name__ == '__main__':
    unittest.main()
