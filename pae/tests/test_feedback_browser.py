"""Real isolated Chromium, actual renderer/card functions, synthetic replies only."""
import json
from pathlib import Path
import sys
import unittest

RESEARCH = Path(__file__).resolve().parents[3]
SOURCE = Path(__file__).resolve().parents[1]
if '--baseline' in sys.argv:
    sys.argv.remove('--baseline')
    SOURCE = RESEARCH / 'repository' / 'pae'
sys.path.insert(0, str(RESEARCH / 'test-tools' / 'python-deps'))
from playwright.sync_api import sync_playwright


class CardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(headless=True)
        sw = (SOURCE / 'extension' / 'sw.js').read_text(encoding='utf-8')
        cls.paint = sw[sw.index('function paeRegisterHighlights('):sw.index('function paeClearHighlights(')]
        cls.clear = sw[sw.index('function paeClearHighlights('):sw.index('var PAE_STREAM =')]
        cls.hover_source = sw[sw.index('function paeHoverInitMain()'):]

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.context = self.browser.new_context(viewport={'width':1000,'height':700}, service_workers='block')
        self.context.route('**/*', lambda r:r.abort())
        self.page = self.context.new_page()
        self.page.set_content('<style>body{margin:150px;font:24px sans-serif}</style><p>synthetic exampleword</p>')
        self.page.evaluate("""() => {
          window.__messageHandlers=new Set();
          const add=window.addEventListener.bind(window), remove=window.removeEventListener.bind(window);
          window.addEventListener=(t,f,...args)=>{if(t==='message')window.__messageHandlers.add(f);return add(t,f,...args);};
          window.removeEventListener=(t,f,...args)=>{if(t==='message')window.__messageHandlers.delete(f);return remove(t,f,...args);};
          window.__feedbackRequests=[];
          window.__recorded=false;
          window.addEventListener('message', e => {
            const d=e.data||{};
            if(d.source!=='pae')return;
            if(d.type==='known_click'||d.type==='known_state')window.__feedbackRequests.push(d);
            if(d.type==='known_state')window.postMessage({source:'pae-feedback',type:'known_state_result',
              request_id:d.request_id,lemma:d.lemma,page_id:d.page_id,ok:true,
              data:{ok:true,lemma:d.lemma,recorded:window.__recorded&&d.lemma==='synthetic',
                valid_until:Math.floor(Date.now()/1000)+3600,s_value:window.__recorded?0.639:0.196}},'*');
          });
        }""")
        self.page.evaluate('(function(){'+self.paint+'\n'+self.hover_source+"""
          paeRegisterHighlights([
            {surface:'synthetic',lemma:'synthetic',gloss:'合成释义',s_value:0.196},
            {surface:'exampleword',lemma:'exampleword',gloss:'第二个词',s_value:0.196}]);
          paeHoverInitMain();
        })()""")
        self.button = self.page.locator('#pae-hover-host button.b')

    def tearDown(self):
        self.context.close()

    def hover(self, index=0):
        rect=self.page.evaluate('(i)=>window.__paeHL.entries[i].range.getBoundingClientRect().toJSON()',index)
        self.page.mouse.move(rect['x']+rect['width']/2,rect['y']+rect['height']/2)
        self.button.wait_for(state='visible')
        self.page.wait_for_function("document.getElementById('pae-hover-host').shadowRoot.querySelector('button.b').textContent !== '查询中…'")

    def acknowledge(self, ok=True, error=''):
        self.page.wait_for_function("window.__feedbackRequests.some(x=>x.type==='known_click')")
        self.page.evaluate("""v=>{
          const q=window.__feedbackRequests.filter(x=>x.type==='known_click').at(-1);
          window.__recorded=v.ok;
          window.postMessage({source:'pae-feedback',type:'known_result',request_id:q.request_id,
            lemma:q.lemma,page_id:q.page_id,ok:v.ok,error:v.error,
            data:{ok:v.ok,error:v.error,counted:v.ok,s_value:0.639,
              feedback:{ok:true,lemma:q.lemma,recorded:v.ok,valid_until:Math.floor(Date.now()/1000)+3600,s_value:0.639}}},'*');
        }""",{'ok':ok,'error':error})

    def test_does_not_confirm_before_backend_reply(self):
        self.hover()
        self.button.click()
        self.assertEqual(self.button.inner_text(),'记录中…')
        self.assertTrue(self.button.is_disabled())

    def test_confirmed_feedback_survives_rehover_and_updates_word_meta(self):
        self.hover(); self.button.click(); self.acknowledge()
        self.page.wait_for_timeout(100)
        self.assertEqual(self.button.inner_text(),'已记录')
        self.page.wait_for_timeout(80)
        self.page.mouse.move(900,600)
        self.page.wait_for_timeout(300)
        self.hover()
        self.assertEqual(self.button.inner_text(),'已记录')
        self.assertTrue(self.button.is_disabled())
        self.assertEqual(self.page.evaluate('Object.values(window.__paeHL.meta).find(m=>m.lemma==="synthetic").s_value'),0.639)

    def test_business_rejection_stays_retryable(self):
        self.hover(); self.button.click(); self.acknowledge(False,'known_click_daily_limit')
        self.page.wait_for_timeout(100)
        self.assertEqual(self.button.inner_text(),'认识了')
        self.assertFalse(self.button.is_disabled())
        self.assertIn('额度',self.page.locator('#pae-hover-host .c').inner_text())

    def test_late_reply_does_not_confirm_a_different_word(self):
        self.hover(); self.button.click()
        self.page.wait_for_timeout(100)
        self.hover(1)
        self.acknowledge()
        self.page.wait_for_timeout(100)
        self.assertEqual(self.button.inner_text(),'认识了')

    def test_unknown_reply_reconciles_without_resubmitting(self):
        self.hover(); self.button.click()
        self.page.evaluate('window.__recorded=true')
        self.page.wait_for_function("document.getElementById('pae-hover-host').shadowRoot.querySelector('.b').textContent==='已记录'",timeout=11000)
        self.assertEqual(self.page.evaluate("window.__feedbackRequests.filter(x=>x.type==='known_click').length"),1)

    def test_already_recorded_word_is_loaded_on_first_hover(self):
        self.page.evaluate('window.__recorded=true')
        self.hover()
        self.assertEqual(self.button.inner_text(),'已记录')
        self.assertTrue(self.button.is_disabled())

    def test_expired_confirmation_can_be_read_again(self):
        self.hover(); self.button.click(); self.acknowledge()
        self.page.wait_for_timeout(100)
        self.page.evaluate('''() => {
          const actualNow=Date.now;
          Date.now=()=>actualNow()+7200000;
          window.__recorded=false;
        }''')
        self.page.mouse.move(900,600);self.page.wait_for_timeout(300)
        self.hover()
        self.assertEqual(self.button.inner_text(),'认识了')
        self.assertFalse(self.button.is_disabled())

    def test_feedback_listener_is_released_on_clear(self):
        self.assertEqual(self.page.evaluate('window.__messageHandlers.size'),2)
        self.page.evaluate('(function(){'+self.clear+'\npaeClearHighlights();})()')
        self.assertEqual(self.page.evaluate('window.__messageHandlers.size'),1,
                         'clearing highlights must not retain the new feedback listener')


if __name__=='__main__':
    unittest.main(verbosity=2)
