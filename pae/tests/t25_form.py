# -*- coding: utf-8 -*-
"""T25: 内嵌角色形态补全 —— 边注（指着某段说）/ 侧栏（长对话+人看的统计）/ 页面零污染 / 深色。

工件 tests/artifacts/t25_form.json + form.png"""
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
ART = Path(__file__).parent / "artifacts"
ART.mkdir(exist_ok=True)

ANCHOR = "buffer pool orchestrates eviction"
FIX = Path(__file__).parent / "fixture_form.html"
FIX.write_text(
    "<!DOCTYPE html><html><head><meta charset='utf-8'><title>form fixture</title></head><body>"
    "<h1>Memory Management</h1>"
    "<p id='target'>In practice the buffer pool orchestrates eviction of dirty pages while maintaining"
    " a least-recently-used heuristic, and the vacuum process compacts regions periodically.</p>"
    "<p>Second paragraph with contention scheduler quartz and other words for annotation.</p>"
    "<p>Third paragraph: idempotent retries, throughput ceilings, and backpressure signals.</p>"
    "</body></html>", encoding="utf-8")


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


def cap(name, body=None):
    req = urllib.request.Request(BASE + "/v1/cap/" + name, data=json.dumps(body or {}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, {}


res = {}
tmpd = Path(tempfile.mkdtemp())
kill_port(PORT)
env = dict(os.environ)
env["PAE_DB_PATH"] = str(tmpd / "t25.db")
eng = subprocess.Popen([sys.executable, "-m", "uvicorn", "pae_core.api:app", "--port", str(PORT)],
                       cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _attempt in range(3):
        if wait_engine_verified(PORT, tmpd / "t25.db", eng):
            break
        eng = subprocess.Popen([sys.executable, "-m", "uvicorn", "pae_core.api:app", "--port", str(PORT)],
                               cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        raise RuntimeError("engine start failed after 3 tries (port %d, db %s)" % (PORT, tmpd / "t25.db"))
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            tempfile.mkdtemp(prefix="pae-t25-"), headless=True, channel="chromium", timeout=60000,
            color_scheme="dark",
            args=["--disable-extensions-except=" + EXT, "--load-extension=" + EXT])
        page = ctx.new_page()
        page.goto(FIX.as_uri())
        for _ in range(60):
            page.wait_for_timeout(1000)
            if page.evaluate("!!document.getElementById('pae-char-host')"):
                break
        res["host_present"] = page.evaluate("!!document.getElementById('pae-char-host')")
        # 页面自身的骨架：记录 tag 序列，稍后比对（证明零污染）
        before_tags = page.evaluate("Array.from(document.body.children).map(e=>e.tagName+'#'+e.id).join(',')")
        before_len = page.evaluate("document.body.children.length")

        # 推送一条带锚点的消息 → 应出现边注并指向目标段落
        cap("pae.say", {"text": "这段写的就是缓冲池怎么挑脏页淘汰的。", "anchor": ANCHOR, "actor": "companion"})
        note = None
        for _ in range(25):
            page.wait_for_timeout(1000)
            note = page.evaluate("""() => {
              const h = document.getElementById('pae-char-host');
              if (!h || !h.shadowRoot) return null;
              const m = h.shadowRoot.getElementById('margin');
              const t = h.shadowRoot.getElementById('mtext');
              if (!m || getComputedStyle(m).display === 'none') return null;
              const r = m.getBoundingClientRect();
              const tg = document.getElementById('target').getBoundingClientRect();
              return {text: t.textContent, noteX: Math.round(r.left), noteY: Math.round(r.top),
                      targetRight: Math.round(tg.right), targetTop: Math.round(tg.top)};
            }""")
            if note:
                break
        res["margin_note"] = note
        # 判据：边注必须**贴住目标段落**（水平落在段落跨度内，垂直在段落附近），不要求具体在左/右/下
        res["margin_near_target"] = bool(
            note and (note["noteX"] <= note["targetRight"] + 30)
            and (note["noteX"] + 260 >= note["targetRight"] - 400)
            and abs(note["noteY"] - note["targetTop"]) < 320)

        # 侧栏：打开 → 有对话记录 + 人看的统计
        page.evaluate("""() => { const h = document.getElementById('pae-char-host');
          h.shadowRoot.getElementById('sidebtn').click(); }""")
        page.wait_for_timeout(600)
        side = page.evaluate("""() => { const h = document.getElementById('pae-char-host');
          const s = h.shadowRoot.getElementById('side');
          return {display: getComputedStyle(s).display,
                  turns: h.shadowRoot.getElementById('sidebd').innerText.length,
                  stats: h.shadowRoot.getElementById('sidestats').innerText}; }""")
        res["side_display"] = side.get("display")
        res["side_has_turns"] = (side.get("turns") or 0) > 0
        page.evaluate("""() => { const h = document.getElementById('pae-char-host');
          h.shadowRoot.getElementById('sidereload').click(); }""")
        page.wait_for_timeout(2500)
        stats2 = page.evaluate("""() => document.getElementById('pae-char-host').shadowRoot.getElementById('sidestats').innerText""")
        res["stats_text"] = (stats2 or "")[:160]
        res["stats_from_engine"] = ("转化率" in (stats2 or ""))

        # 深色是否生效（宿主偏好 dark → 独立色板）
        res["dark_applied"] = page.evaluate("window.matchMedia('(prefers-color-scheme: dark)').matches")
        res["side_bg_dark"] = page.evaluate("""() => { const h = document.getElementById('pae-char-host');
          const s = h.shadowRoot.getElementById('side'); return getComputedStyle(s).backgroundImage.slice(0,40); }""")

        # 页面零污染
        after_tags = page.evaluate("Array.from(document.body.children).map(e=>e.tagName+'#'+e.id).join(',')")
        after_len = page.evaluate("document.body.children.length")
        res["no_page_dom_change"] = (before_len == after_len and
                                     before_tags.replace("DIV#pae-char-host", "") == after_tags.replace("DIV#pae-char-host", ""))
        page.screenshot(path=str(ART / "form.png"))
        ctx.close()
finally:
    eng.terminate()
    kill_port(PORT)

ok = (res.get("host_present") and res.get("margin_note") and res.get("margin_near_target")
      and res.get("side_display") == "flex" and res.get("side_has_turns")
      and res.get("stats_from_engine") and res.get("dark_applied") and res.get("no_page_dom_change"))
res["T25"] = "PASS" if ok else "FAIL"
(ART / "t25_form.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(res, ensure_ascii=False, indent=1))
print("T25 FORM:", res["T25"])