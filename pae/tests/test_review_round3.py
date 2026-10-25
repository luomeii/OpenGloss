"""Small third-review boundary fixes; no real configuration or keys are read."""
from pathlib import Path
import hashlib, json, sys, tempfile, unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pae_core import keys, privacy


class ReviewBoundaryContracts(unittest.TestCase):
    def privacy_at(self, text):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'config.json';path.write_text(text,encoding='utf-8')
            with patch.object(privacy,'config_path',return_value=path):return privacy.settings()

    def test_omitted_privacy_keeps_existing_full_default(self):
        self.assertEqual(self.privacy_at('{}')['url_storage'],'full')

    def test_invalid_explicit_url_mode_does_not_expand_storage(self):
        with self.assertLogs('pae_core.privacy',level='WARNING'):
            cfg=self.privacy_at('{"privacy":{"url_storage":"typo"}}')
        self.assertEqual(cfg['url_storage'],'off')

    def test_malformed_config_and_privacy_shapes_fail_closed(self):
        for text in ('{broken', '[]', '{"privacy":[]}', '{"privacy":null}'):
            with self.subTest(text=text), patch.object(privacy,'_SETTINGS_WARNING',False,create=True):
                cfg=self.privacy_at(text)
                self.assertEqual(cfg['url_storage'],'off')
                self.assertFalse(cfg['context_storage'])
                self.assertFalse(cfg['shadow_model'])

    def test_scoped_digest_uses_constant_time_compare(self):
        token='synthetic-scoped-token'
        digest=hashlib.sha256(token.encode()).hexdigest()
        with patch.object(keys,'_load',return_value={'actors':{'ext:synthetic':{
            'key_sha256':digest,'scopes':['read']}}}), \
             patch('hmac.compare_digest',return_value=True) as compare:
            self.assertEqual(keys.resolve(token),('ext:synthetic',['read']))
        compare.assert_called_once_with(digest,digest)

    def test_malformed_key_records_are_rejected_without_exception(self):
        documents=([],{'actors':[]},{'actors':{'ext:bad':None}},
                   {'actors':{'ext:bad':{'key_sha256':[]}}},
                   {'actors':{'ext:bad':{'key_sha256':'not-a-digest'}}})
        for document in documents:
            with self.subTest(document=document),patch.object(keys,'_load',return_value=document):
                self.assertEqual(keys.resolve('synthetic-unknown'),(None,None))


if __name__=='__main__':unittest.main()
