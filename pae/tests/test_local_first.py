"""Zero-configuration HTTP contracts; every request uses synthetic engine data."""
from pathlib import Path
import os, sys, tempfile, unittest
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
TEMP=tempfile.TemporaryDirectory(prefix='local-first-contract-')
os.environ['PAE_CONFIG_PATH']=str(Path(TEMP.name)/'config.json')
os.environ['PAE_DB_PATH']=str(Path(TEMP.name)/'synthetic.db')
os.environ['PAE_KEYS_PATH']=str(Path(TEMP.name)/'synthetic-keys.json')
Path(os.environ['PAE_CONFIG_PATH']).write_text('{}',encoding='utf-8')
from fastapi.testclient import TestClient
from pae_core import api


class LocalFirstContracts(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(api.app,base_url='http://127.0.0.1:4815')
        self.addCleanup(self.client.close)
        self.engine=Mock();self.engine.annotate.return_value=[{'lemma':'vessel','gloss':'synthetic'}]
        self.engine.record_event.return_value={'ok':True,'counted':True}
        patcher=patch.object(api,'get_engine',return_value=self.engine)
        patcher.start();self.addCleanup(patcher.stop)

    def test_fresh_local_extension_can_annotate_without_any_credentials(self):
        response=self.client.post('/v1/annotate',json={'text':'The vessel crosses the ocean.'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()[0]['lemma'],'vessel')

    def test_local_event_has_internal_user_identity_without_bearer(self):
        response=self.client.post('/v1/event',json={'type':'known_click','lemma':'vessel'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.engine.record_event.call_args.kwargs['source'],'local')

    def test_capability_uses_existing_local_identity_without_pairing(self):
        with patch.object(api.capsurface,'handle',return_value=(200,{'ok':True})) as handle:
            response=self.client.post('/v1/cap/pae.status',json={})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(handle.call_args.kwargs['authenticated_identity'][0],'local')

    def test_external_host_is_rejected_before_engine(self):
        response=self.client.post('/v1/annotate',headers={'Host':'foreign.invalid:4815'},json={'text':'synthetic'})
        self.assertEqual(response.status_code,403)
        self.engine.annotate.assert_not_called()

    def test_external_origin_is_rejected_before_engine(self):
        response=self.client.post('/v1/annotate',headers={'Origin':'https://foreign.invalid'},json={'text':'synthetic'})
        self.assertEqual(response.status_code,403)
        self.engine.annotate.assert_not_called()

    def test_cross_site_request_without_origin_is_rejected(self):
        response=self.client.get('/v1/status',headers={'Sec-Fetch-Site':'cross-site'})
        self.assertEqual(response.status_code,403)

    def test_form_post_cannot_trigger_business_operation(self):
        response=self.client.post('/v1/annotate',content='text=synthetic',headers={'Content-Type':'application/x-www-form-urlencoded'})
        self.assertEqual(response.status_code,415)
        self.engine.annotate.assert_not_called()

    def test_extension_origin_can_annotate_without_secret(self):
        response=self.client.post('/v1/annotate',headers={'Origin':'chrome-extension://'+'a'*32},json={'text':'synthetic'})
        self.assertEqual(response.status_code,200,response.text)

    def test_user_interfaces_contain_no_pairing_fields_or_bearer_headers(self):
        root=Path(__file__).resolve().parents[2]
        for relative in ('pae/extension/sw.js','pae/extension/popup.js','pae/extension/popup.html','pae/panel.html','distribution/manage.js','distribution/manage.html'):
            text=(root/relative).read_text(encoding='utf-8')
            with self.subTest(path=relative):
                for marker in ('paeToken','PAE_TOKEN','PANEL_TOKEN','pae-token','copy-token','engine-token','Authorization','X-OpenGloss-Token'):
                    self.assertFalse(marker in text,marker+' present in '+relative)


if __name__=='__main__':
    try:unittest.main()
    finally:TEMP.cleanup()
