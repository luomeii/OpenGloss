from pathlib import Path
import io, sys, unittest, urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from distribution import bootstrap


class ProbeRedirectBoundary(unittest.TestCase):
    def test_loopback_probe_redirect_is_rejected_and_response_closed(self):
        handler = bootstrap.NoRedirect()
        response = io.BytesIO(b'synthetic redirect')
        request = urllib.request.Request('http://127.0.0.1:54975/v1/health',
            headers={'Authorization': 'Bearer ' + 'x' * 43})
        with self.assertRaises(ValueError):
            handler.redirect_request(request, response, 302, 'redirect', {},
                'https://unrelated.example/sink')
        self.assertTrue(response.closed)


if __name__ == '__main__':
    unittest.main()
