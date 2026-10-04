"""T2 behavior test: two annotation batches must ACCUMULATE (review P0-1 regression).

Evidence artifact: tests/artifacts/t2_accumulate.json
"""
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
ART = Path(__file__).parent / 'artifacts'
ART.mkdir(exist_ok=True)


def kill_port(port):
    out = subprocess.run(['netstat', '-ano', '-p', 'TCP'], capture_output=True, text=True, errors='replace', timeout=20).stdout
    pids = {p.split()[4] for p in out.splitlines()
            if len(p.split()) >= 5 and p.split()[1].endswith(':' + str(port)) and p.split()[3] == 'LISTENING'}
    for pid in pids:
        subprocess.run(['taskkill', '/F', '/PID', pid], capture_output=True)
    time.sleep(1)


def start_engine(db):
    env = dict(os.environ)
    env['PAE_DB_PATH'] = str(db)
    return subprocess.Popen([sys.executable, '-m', 'uvicorn', 'pae_core.api:app', '--port', str(PORT)],
                            cwd=str(ROOT), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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


def wait_health(t=60):
    end = time.time() + t
    while time.time() < end:
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/v1/health', timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def read_dbg(page):
    raw = page.evaluate("document.documentElement.dataset.paeDebug || ''")
    try:
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def hl_state(page):
    return page.evaluate("""() => {
      const h = (CSS.highlights && CSS.highlights.get('pae-word'));
      if (!h) return { size: 0, texts: [] };
      const arr = []; h.forEach(r => arr.push(r.toString()));
      return { size: h.size, texts: arr };
    }""")


def wait_for(page, pred, t=45):
    end = time.time() + t
    while time.time() < end:
        page.wait_for_timeout(800)
        s = hl_state(page)
        if pred(s):
            return s
    return None


kill_port(PORT)
db = Path(tempfile.mkdtemp()) / 't2.db'
eng = start_engine(db)
for _attempt in range(3):
    if wait_engine_verified(PORT, db, eng, timeout=60):
        break
    eng = start_engine(db)
else:
    raise RuntimeError("engine start failed after 3 tries (port %d, db %s)" % (PORT, db))
result = {}
try:
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            tempfile.mkdtemp(prefix='pae-t2-'), headless=True, channel='chromium', timeout=60000,
            args=['--disable-extensions-except=' + EXT, '--load-extension=' + EXT])
        page = ctx.new_page()
        page.goto(FIX.as_uri())
        s1 = wait_for(page, lambda s: s['size'] > 0)
        assert s1, 'batch 1 produced no highlight'
        dbg1 = read_dbg(page)
        b1_surfaces = [a['surface'] for a in (dbg1.get('lastResponse') or [])]
        result['batch1'] = {'size': s1['size'], 'surfaces': b1_surfaces}

        # 第二批：注入全新段落（不同词，确保是新 range 而不是重复）
        js = ''.join(["document.getElementById('inject').innerHTML = ",
                       "\"<p>" + 'A ubiquitous tenacious meticulous redundant ambiguity persisted.</p>'.replace("'", "") + "\";"])
        page.evaluate(js)
        s2 = wait_for(page, lambda s: s['size'] > s1['size'], t=45)
        dbg2 = read_dbg(page)
        result['batch2'] = {'size': s2['size'] if s2 else -1,
                            'rendered_field': (dbg2.get('rendered') or {}),
                            'surfaces': [a['surface'] for a in (dbg2.get('lastResponse') or [])]}

        # 断言1：累积（第二批之后总量 = 第一批 + 新增）
        accum_ok = bool(s2) and s2['size'] > s1['size']
        # 断言2：第一批的词仍然有高亮（这就是「第一批全消失」的回归点）
        still = [w for w in b1_surfaces if any(w == t.strip() for t in (s2['texts'] if s2 else []))]
        result['batch1_words_still_highlighted'] = still
        result['accumulation_ok'] = accum_ok
        result['first_batch_preserved'] = len(still) > 0

        # 断言3：重复调用不产生重复 range（幂等）
        dup = page.evaluate("""() => {
          const h = CSS.highlights.get('pae-word');
          const seen = {}; let d = 0;
          h.forEach(r => { const k = r.startContainer.parentElement ? '' : ''; });
          const arr = []; h.forEach(r => arr.push(r));
          const keys = arr.map(r => {
            const p = r.getBoundingClientRect();
            return Math.round(p.top) + ':' + Math.round(p.left) + ':' + r.toString();
          });
          const set = new Set(keys);
          return { total: keys.length, unique: set.size };
        }""")
        result['dedup'] = dup
        result['dedup_ok'] = dup['total'] == dup['unique']
        ctx.close()
finally:
    eng.terminate()
    kill_port(PORT)
    subprocess.Popen([sys.executable, '-m', 'uvicorn', 'pae_core.api:app', '--port', str(PORT)],
                     cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

allok = (result.get('accumulation_ok') and result.get('first_batch_preserved') and result.get('dedup_ok'))
result['T2'] = 'PASS' if allok else 'FAIL'
(ART / 't2_accumulate.json').write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
print(json.dumps({k: result[k] for k in ('batch1', 'batch2', 'accumulation_ok', 'first_batch_preserved', 'dedup_ok', 'T2') if k in result}, ensure_ascii=False))
print('T2 ACCUMULATION:', result['T2'])