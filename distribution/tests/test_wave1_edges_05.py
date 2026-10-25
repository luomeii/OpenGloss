from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import ssl, sys, tempfile, threading, unittest, urllib.error
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from distribution import bootstrap, network, package


class FirstWaveEdges(unittest.TestCase):
    def test_parallel_environment_preparation_needs_no_credential_write(self):
        with tempfile.TemporaryDirectory() as temp:
            data=Path(temp)
            with ThreadPoolExecutor(max_workers=2) as pool:
                values=list(pool.map(lambda _:bootstrap.engine_environment(data/'version',data,54975,'a'*32),range(2)))
            self.assertEqual(values[0],values[1])
            self.assertEqual(list(data.iterdir()),[])

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
