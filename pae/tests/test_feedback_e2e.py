"""Isolated extension -> real API/Engine -> synthetic dictionary/learning DB.

Only the test extension copy's default port is changed. No user browser,
default service port, real model key, or original test launcher is used.
"""
from contextlib import closing
import http.client
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time

RESEARCH=Path(__file__).resolve().parents[3]
SOURCE=Path(__file__).resolve().parents[1]
BASELINE='--baseline' in sys.argv
if BASELINE: SOURCE=RESEARCH/'repository'/'pae'
sys.path.insert(0,str(RESEARCH/'test-tools'/'python-deps'))
from playwright.sync_api import sync_playwright

SERVER_CODE=r'''
import os,sys
for key in list(os.environ):
    if key.startswith('PAE_') or any(t in key.upper() for t in ('API_KEY','TOKEN','SECRET')):
        os.environ.pop(key,None)
os.environ.update(PAE_DB_PATH=sys.argv[2], PAE_DICT_PATH=sys.argv[3], PAE_LEMMA_PATH=sys.argv[4])
sys.path.insert(0,sys.argv[1])
import urllib.request
def blocked(*a,**k):raise RuntimeError('model networking disabled in isolated test')
urllib.request.urlopen=blocked
from pae_core.api import app
from fastapi.responses import HTMLResponse
@app.get('/fixture/{name}',response_class=HTMLResponse)
def fixture(name):
    return '<html><head><title>Isolated feedback fixture</title></head><body style="margin:40px;height:2000px;font:24px sans-serif"><div style="height:220px"></div><p>Readers encounter synthetic while reading this deliberately simple isolated paragraph.</p></body></html>'
import uvicorn
uvicorn.run(app,host='127.0.0.1',port=int(sys.argv[5]),log_level='warning',timeout_graceful_shutdown=1)
'''


def request(port,path):
    with closing(http.client.HTTPConnection('127.0.0.1',port,timeout=2)) as c:
        c.request('GET',path)
        r=c.getresponse()
        return r.status,json.loads(r.read())


def run():
    artifacts=RESEARCH/'test-artifacts'/'feedback'
    artifacts.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='e2e-',dir=artifacts) as tmp:
        root=Path(tmp).resolve()
        assert artifacts.resolve() in root.parents
        dictionary=root/'dictionary.sqlite'
        with closing(sqlite3.connect(dictionary)) as c:
            c.execute('CREATE TABLE dict_words(word TEXT PRIMARY KEY,translation TEXT,bnc INTEGER)')
            c.execute('INSERT INTO dict_words VALUES (?,?,?)',('synthetic','adj. 合成的',0))
            c.execute('CREATE TABLE exchange_map(inflected TEXT,base TEXT)')
            c.commit()
        lemma=root/'lemma.txt';lemma.write_text('synthetic/1 -> synthetics\n',encoding='utf-8')
        learning=root/'learning.sqlite'
        with closing(socket.socket()) as s:
            s.bind(('127.0.0.1',0));port=s.getsockname()[1]
        assert port!=4815
        extension=root/'extension'
        shutil.copytree(SOURCE/'extension',extension)
        for name in ('sw.js','popup.js','manifest.json'):
            p=extension/name
            p.write_text(p.read_text(encoding='utf-8').replace('http://127.0.0.1:4815',f'http://127.0.0.1:{port}'),encoding='utf-8')
        env={k:v for k,v in os.environ.items() if not k.startswith('PAE_') and
             not any(t in k.upper() for t in ('API_KEY','TOKEN','SECRET'))}
        process=None;log=None;context=None

        def start():
            nonlocal process,log
            log=open(root/'server.log','ab')
            process=subprocess.Popen([sys.executable,'-I','-B','-c',SERVER_CODE,str(SOURCE),
                str(learning),str(dictionary),str(lemma),str(port)],cwd=root,env=env,
                stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            deadline=time.monotonic()+15
            while time.monotonic()<deadline:
                if process.poll() is not None:
                    raise AssertionError('owned server failed: '+(root/'server.log').read_text(encoding='utf-8',errors='replace')[-1800:])
                try:
                    status,data=request(port,'/v1/health')
                    if status==200:
                        assert Path(data['db']).resolve()==learning.resolve()
                        return
                except (OSError,ValueError): pass
                time.sleep(.1)
            raise AssertionError('owned test server did not become ready')

        def stop():
            nonlocal process,log
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                    try:process.wait(timeout=5)
                    except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
                process=None
            if log is not None:log.close();log=None

        def count_known():
            with closing(sqlite3.connect(learning)) as c:
                return c.execute("SELECT COUNT(*) FROM events WHERE type='known_click'").fetchone()[0]

        def open_card(page):
            page.wait_for_function('window.__paeHL && window.__paeHL.hoverBound && window.__paeHL.entries.length>0',timeout=10000)
            r=page.evaluate('window.__paeHL.entries[0].range.getBoundingClientRect().toJSON()')
            page.mouse.move(r['x']+r['width']/2,r['y']+r['height']/2)
            button=page.locator('#pae-hover-host button.b')
            button.wait_for(state='visible',timeout=5000)
            page.wait_for_function("document.getElementById('pae-hover-host').shadowRoot.querySelector('.b').textContent!=='查询中…'",timeout=5000)
            return button

        try:
            start()
            with sync_playwright() as pw:
                context=pw.chromium.launch_persistent_context(str(root/'browser-profile'),channel='chromium',
                    headless=True,args=[f'--disable-extensions-except={extension}',f'--load-extension={extension}',
                    '--disable-background-networking','--no-first-run'])
                context.route('**/*',lambda route:route.continue_() if route.request.url.startswith(f'http://127.0.0.1:{port}/') else route.abort())
                if not context.service_workers:context.wait_for_event('serviceworker',timeout=5000)
                page=context.new_page()
                page.goto(f'http://127.0.0.1:{port}/fixture/one')
                button=open_card(page)
                assert button.inner_text()=='认识了'
                button.click()
                page.wait_for_function("document.getElementById('pae-hover-host').shadowRoot.querySelector('.b').textContent==='已记录'",timeout=5000)
                assert count_known()==1
                if not BASELINE:page.screenshot(path=str(artifacts/'feedback-confirmed.png'))
                page.wait_for_timeout(100)
                page.mouse.move(900,600);page.wait_for_timeout(300)
                assert open_card(page).inner_text()=='已记录'
                page.reload()
                assert open_card(page).inner_text()=='已记录'
                page.goto(f'http://127.0.0.1:{port}/fixture/two')
                assert open_card(page).inner_text()=='已记录'
                assert count_known()==1
                stop();start()
                page.goto(f'http://127.0.0.1:{port}/fixture/three')
                button=open_card(page)
                assert button.inner_text()=='已记录'
                assert button.is_disabled()
                assert count_known()==1
                if not BASELINE:
                    status,data=request(port,'/v1/feedback?lemma=synthetic')
                    assert status==200 and data['recorded'] is True
                context.close();context=None
            result={'real_extension_to_real_api':True,'synthetic_dictionary_and_database':True,
                'normal_click':True,'rehover':True,'reload':True,'cross_page':True,
                'actual_server_process_restart':True,'known_events':1,'used_default_port':False,
                'real_model_calls':False,'baseline':BASELINE}
            (artifacts/'e2e-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            print(json.dumps(result,ensure_ascii=False))
        finally:
            if context is not None:context.close()
            stop()


if __name__=='__main__':run()
