from pathlib import Path
import json, sys, tempfile, threading, time, unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from distribution import bootstrap


class PartialCredentialWrite(unittest.TestCase):
    def test_existing_file_being_written_is_read_after_completion(self):
        with tempfile.TemporaryDirectory() as temp:
            data = Path(temp)
            target = data / 'http-auth.json'
            target.touch()
            def finish():
                time.sleep(.05)
                target.write_text(json.dumps({'token': 'c' * 43}), encoding='utf-8')
            writer = threading.Thread(target=finish)
            writer.start()
            try:
                self.assertEqual(bootstrap.engine_token(data), 'c' * 43)
            finally:
                writer.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
