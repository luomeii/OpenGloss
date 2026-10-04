"""Toggle acceptance: on->annotations render; off->DOM zero residue; on->recovers."""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
EXT = str(ROOT / 'extension')
PORT = 4815
FIX = Path(__file__).parent / 'fixture_m2.html'


def kill_port(port):
    out = subprocess.run(['netstat', '-ano', '-p', 'TCP'], capture_output=True, text=True, timeout=20).stdout
    pids = {p.split()[4] for p in out.splitlines() if len(p.split()) >= 5 and p.split()[1].endswith(':' + str(port)) and p.split()[3] == 'LISTENING'}
    for pid in pids:
        subprocess.run(['taskkill', '/F', '/PID', pid], capture_output=True)
    time.sleep(1)


def wait_engine_verified(port, db_path, eng=None, tries=3, timeout=90):
    """等引擎就绪并**验证身份**：/v1/health 返回的 db 必须等于本测试的临时库路径。
    端口 4815 由 11 个测试共用，任何一个崩溃残留的僵尸引擎都会让后续测试静默拿到 0 条注解
    （2026-10-02 实锤：清僵尸后 e2e 立即恢复）。这里撞上僵尸就杀掉重试，把静默级联失败变成自愈。"""
    import urllib.request, json, time, subprocess
    for attempt in range(tries):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/v1/health" % port, timeout=3) as r:
                    h = json.loads(r.read().decode("utf-8"))
                if str(h.get("db")).replace(chr(92), "/") == str(db_path).replace(chr(92), "/"):
                    return True                      # 身份正确：就是我们的引擎
                # 身份不符 = 撞上僵尸：杀掉后重起
                break
            except Exception:
                time.sleep(0.5)
        # 端口上有别人的引擎（或我们的没起来）：清理后由调用方重起
        _kill_port_4815(port)
        if eng is not None and attempt < tries - 1:
            try:
                eng.terminate()
            except Exception:
                pass
            return False   # 返回 False 让调用方重新 Popen 并再次调用本函数
    raise RuntimeError("engine identity check failed on port %d (expect db=%s)" % (port, db_path))


_kill_port_4815 = kill_port


def start_engine(db):
    env = dict(os.environ); env['PAE_DB_PATH'] = str(db)
    return subprocess.Popen([sys.executable, '-m', 'uvicorn', 'pae_core.api:app', '--port', str(PORT)], cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_health(t=60):
    end = time.time() + t
    while time.time() < end:
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/v1/health', timeout=3) as r:
                if r.status == 200: return True
        except Exception: time.sleep(0.5)
    return False


def state(page):
    return page.evaluate("""() => ({
      hl: (CSS.highlights && CSS.highlights.get('pae-word')) ? CSS.highlights.get('pae-word').size : -1,
      style: !!document.getElementById('pae-highlight-style'),
      titles: document.querySelectorAll('[data-pae-title]').length,
    })""")


def wait_for(page, cond, t=45):
    end = time.time() + t
    while time.time() < t + time.time() if False else time.time() < end:
        page.wait_for_timeout(800)
        s = state(page)
        if cond(s): return s
    return None


kill_port(PORT)
_tgdb = Path(tempfile.mkdtemp()) / 'tg.db'
eng = start_engine(_tgdb)
for _attempt in range(3):
    if wait_engine_verified(PORT, _tgdb, eng):
        break
    eng = start_engine(_tgdb)
else:
    raise RuntimeError('engine start failed after 3 tries (port %d, db %s)' % (PORT, _tgdb))
try:
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            tempfile.mkdtemp(prefix='pae-tg-'), headless=True, channel='chromium', timeout=60000,
            args=['--disable-extensions-except=' + EXT, '--load-extension=' + EXT])
        page = ctx.new_page()
        page.goto(FIX.as_uri())
        s1 = wait_for(page, lambda s: s['hl'] > 0)
        assert s1, 'no initial highlight'
        text_on = page.evaluate('document.body.innerText')

        sw = None
        for _ in range(20):
            for w in ctx.service_workers:
                if 'sw.js' in (w.url or ''): sw = w; break
            if sw: break
            page.wait_for_timeout(500)
        assert sw, 'no service worker'
        sw.evaluate("chrome.storage.local.set({ paeEnabled: false })")

        s2 = wait_for(page, lambda s: s['hl'] == -1 and not s['style'] and s['titles'] == 0)
        text_off = page.evaluate('document.body.innerText') if s2 else ''

        sw.evaluate("chrome.storage.local.set({ paeEnabled: true })")
        s3 = wait_for(page, lambda s: s['hl'] > 0)
        text_on2 = page.evaluate('document.body.innerText') if s3 else ''

        res = {
            'on_hl': s1['hl'], 'on_titles': s1['titles'],
            'off_clean': bool(s2),
            'off_hl': s2['hl'] if s2 else None,
            'off_style': s2['style'] if s2 else None,
            'off_titles': s2['titles'] if s2 else None,
            're_on_hl': s3['hl'] if s3 else -1,
            'text_on_len': len(text_on), 'text_off_len': len(text_off),
            'text_on2_len': len(text_on2),
            'text_stable': text_on == text_off and text_on == text_on2,
        }
        print(json.dumps(res, ensure_ascii=False))
        ok = res['on_hl'] > 0 and res['off_clean'] and res['re_on_hl'] > 0 and res['text_stable']
        print('TOGGLE ACCEPTANCE:', 'PASS' if ok else 'FAIL')
        ctx.close()
finally:
    eng.terminate(); kill_port(PORT)
    subprocess.Popen([sys.executable, '-m', 'uvicorn', 'pae_core.api:app', '--port', str(PORT)], cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# R7: PASS 必须有 JSON 工件支撑（此前只活在日志最后一行），并给出真实退出码。
ART = Path(__file__).parent / 'artifacts'
ART.mkdir(exist_ok=True)
res['run_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
res['measurement'] = '关→DOM 零残留（CSS.highlights/style[ id ]/data-pae-title 三信号）→ 重开恢复'
res['TOGGLE'] = 'PASS' if ok else 'FAIL'
(ART / 'toggle_probe.json').write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding='utf-8')
sys.exit(0 if ok else 1)