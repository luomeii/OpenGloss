from pathlib import Path
import io, sys, unittest, urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from distribution import network


class NetworkResourceBoundary(unittest.TestCase):
    def test_rejected_public_redirect_closes_response(self):
        response = io.BytesIO(b'synthetic')
        request = urllib.request.Request('https://github.com/luomeii/OpenGloss')
        with self.assertRaises(ValueError):
            network.Redirects().redirect_request(request,response,302,'redirect',{},'https://other.example/path')
        self.assertTrue(response.closed)


if __name__ == '__main__': unittest.main()
