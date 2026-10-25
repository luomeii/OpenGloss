from pathlib import Path
import json, sys, tempfile, threading, time, unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from distribution import bootstrap


class PartialCredentialWrite(unittest.TestCase):
    def test_local_environment_never_creates_or_reads_root_credential(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            from unittest.mock import patch
            with patch.object(Path,'read_text',side_effect=AssertionError('credential must not be read')):
                env=bootstrap.engine_environment(root/'version',root,54975,'a'*32)
            self.assertNotIn('PAE_HTTP_TOKEN',env)
            self.assertEqual(env['PAE_INSTANCE_ID'],'a'*32)
            self.assertFalse((root/'http-auth.json').exists())


if __name__ == '__main__':
    unittest.main()
