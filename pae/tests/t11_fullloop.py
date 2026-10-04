# -*- coding: utf-8 -*-
"""T11: 全流程闭环 —— 浏览器交互 → 事件落库 → 指标/上下文/单词状态 全部反映。

这是"各机制与算法正常作用、闭环转起来"的端到端证据。
工件 tests/artifacts/t11_fullloop.json
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
EXT = str(ROOT / "extension")
PORT = 4815
BASE = "http://127.0.0.1:%d" % PORT
FIX = Path(__file__).parent / "fixture_m2.html"
ART = Path(__file__).parent / "artifacts"
ART.mkdir(exist_ok=True)


def kill_port(port):
    out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=20).stdout
    pids = {p.split()[4] for p in out.splitlines()
            if len(p.split()) >= 5 and p.split()[1].endswith(":" + str(port)) and p.split()[3] == "LISTENING"}
    for pid in pids:
        subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
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
    env = dict(os.environ); env["PAE_DB_PATH"] = str(db)
    return subprocess.Popen([sys.executable, "-m", "uvicorn", "pae_core.api:app", "--port", str(PORT)],
                            cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_health(t=60):
    end = time.time() + t
    while time.time() < end:
        try:
            with urllib.request.urlopen(BASE + "/v1/health", timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def call(path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


res = {}
db = Path(tempfile.mkdtemp()) / "t11.db"
kill_port(PORT)
eng = start_engine(db)
for _attempt in range(3):
    if wait_engine_verified(PORT, db, eng):
        break
    eng = start_engine(db)
else:
    raise RuntimeError("engine start failed after 3 tries (port %d, db %s)" % (PORT, db))
try:
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            tempfile.mkdtemp(prefix="pae-t11-"), headless=True, channel="chromium", timeout=60000,
            args=["--disable-extensions-except=" + EXT, "--load-extension=" + EXT])
        page = ctx.new_page()
        page.goto(FIX.as_uri())
        for _ in range(90):
            page.wait_for_timeout(1000)
            if page.evaluate("CSS.highlights && CSS.highlights.get('pae-word') ? CSS.highlights.get('pae-word').size : 0") > 0:
                break
        res["highlights_after_load"] = page.evaluate(
            "CSS.highlights && CSS.highlights.get('pae-word') ? CSS.highlights.get('pae-word').size : 0")

        # 悬停第一个词 → 卡片
        rects = page.evaluate("""() => {
          const h = CSS.highlights.get('pae-word'); const out = [];
          h.forEach(r => { const b = r.getBoundingClientRect();
            if (b.width > 4) out.push({x: b.left + b.width/2, y: b.top + b.height/2}); });
          return out; }""")
        assert rects, "无高亮词"
        page.mouse.move(rects[0]["x"], rects[0]["y"])
        page.wait_for_timeout(900)
        card = page.evaluate("""() => {
          const h = document.getElementById('pae-hover-host');
          const c = h && h.shadowRoot ? h.shadowRoot.getElementById('c') : null;
          return c ? { display: getComputedStyle(c).display, text: c.innerText } : null; }""")
        res["card_text"] = (card or {}).get("text", "")
        first_line = (res["card_text"].split(chr(10))[0] if res["card_text"] else "")
        word = first_line.split("→")[-1].strip().lower()
        res["word_probed"] = word

        # 点「认识了」
        clicked = page.evaluate("""() => {
          const h = document.getElementById('pae-hover-host');
          const b = h && h.shadowRoot ? h.shadowRoot.querySelector('.b') : null;
          if (b) { b.click(); return true; } return false; }""")
        res["clicked_known"] = clicked
        page.wait_for_timeout(2000)
        ctx.close()

    # 闭环验证：引擎必须还活着，且指向临时库（否则查的是生产库，数字全错）
    st, m = call("/v1/cap/pae.metrics")
    mb = m.get("result") or {}
    res["metrics"] = {"exposure": mb.get("annotation_exposure"), "hover_rate": mb.get("hover_rate"),
                      "click_rate": mb.get("click_rate"), "top_ignored": len(mb.get("top_ignored_words") or [])}

    st2, w = call("/v1/cap/pae.word", {"lemma": res["word_probed"]})
    wb = w.get("result") or {}
    res["word_state"] = {k: wb.get(k) for k in ("lemma", "s_value", "band", "hover", "known_click")}

    st3, cx = call("/v1/cap/pae.context", {"top_n": 10})
    cb = cx.get("result") or {}
    res["context"] = {"has_persona": bool(((cb.get("persona") or {}).get("prompt"))),
                      "words": len(cb.get("words") or []),
                      "band": (cb.get("learner") or {}).get("band")}
    st4, dc = call("/v1/cap/pae.audit")   # 调用审计（pae.decisions 已专用于决策日志）
    res["audit_rows"] = len(dc.get("result") or [])
finally:
    eng.terminate(); kill_port(PORT)
    subprocess.Popen([sys.executable, "-m", "uvicorn", "pae_core.api:app", "--port", str(PORT)],
                     cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

ok = (res.get("highlights_after_load", 0) > 0 and res.get("clicked_known")
      and (res.get("metrics", {}).get("exposure") or 0) > 0
      and res.get("word_state", {}).get("known_click") == 1
      and res["context"]["has_persona"] and res.get("audit_rows", 0) > 0)
res["T11"] = "PASS" if ok else "FAIL"
(ART / "t11_fullloop.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(res, ensure_ascii=False, indent=1))
print("T11 FULL LOOP:", res["T11"])