# -*- coding: utf-8 -*-
"""T7: 词级悬停卡 e2e — 悬停出卡/换词换内容/移开消失/「认识了」入库/曝光上报。
工件：tests/artifacts/t7_hover.json + hover_card.png
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
EXT = str(ROOT / "extension")
PORT = 4815
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


def start_engine(db):
    env = dict(os.environ)
    env["PAE_DB_PATH"] = str(db)
    return subprocess.Popen([sys.executable, "-m", "uvicorn", "pae_core.api:app", "--port", str(PORT)],
                            cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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
            with urllib.request.urlopen("http://127.0.0.1:%d/v1/health" % PORT, timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


CARD_JS = """() => {
  const h = document.getElementById('pae-hover-host');
  if (!h || !h.shadowRoot) return null;
  const c = h.shadowRoot.getElementById('c');
  if (!c) return null;
  return { display: getComputedStyle(c).display, text: c.innerText };
}"""

RECTS_JS = """() => {
  const h = CSS.highlights && CSS.highlights.get('pae-word');
  if (!h) return [];
  const out = [];
  h.forEach(r => { const b = r.getBoundingClientRect();
    if (b.width > 4 && b.height > 0) out.push({ x: b.left + b.width / 2, y: b.top + b.height / 2, t: r.toString() }); });
  return out;
}"""

res = {}
tmpd = Path(tempfile.mkdtemp())
db = tmpd / "t7.db"
kill_port(PORT)
eng = start_engine(db)
for _attempt in range(3):
    if wait_engine_verified(PORT, db, eng, timeout=60):
        break
    eng = start_engine(db)
else:
    raise RuntimeError("engine start failed after 3 tries (port %d, db %s)" % (PORT, db))
try:
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            tempfile.mkdtemp(prefix="pae-t7-"), headless=True, channel="chromium", timeout=60000,
            args=["--disable-extensions-except=" + EXT, "--load-extension=" + EXT])
        page = ctx.new_page()
        page.goto(FIX.as_uri())
        for _ in range(90):
            page.wait_for_timeout(1000)
            if page.evaluate("CSS.highlights && CSS.highlights.get('pae-word') ? CSS.highlights.get('pae-word').size : 0") > 0:
                break
        rects = page.evaluate(RECTS_JS)
        res["highlighted_words"] = len(rects)
        res["hl_size"] = page.evaluate("CSS.highlights && CSS.highlights.get('pae-word') ? CSS.highlights.get('pae-word').size : -1")
        res["view"] = page.evaluate("({w: window.innerWidth, h: window.innerHeight, sy: window.scrollY})")
        res["rects_sample"] = [{"x": round(r["x"]), "y": round(r["y"]), "t": r["t"]} for r in rects[:6]]
        if len(rects) < 2:
            page.screenshot(path=str(ART / "t7_debug.png"))
            raise AssertionError("需要至少两个高亮词, got %d (hl_size=%s)" % (len(rects), res["hl_size"]))

        # 悬停第 1 个词
        page.mouse.move(rects[0]["x"], rects[0]["y"])
        page.wait_for_timeout(800)
        c1 = page.evaluate(CARD_JS)
        res["card_visible_1"] = bool(c1 and c1["display"] == "block")
        res["card_text_1"] = (c1 or {}).get("text", "")
        page.screenshot(path=str(ART / "hover_card.png"))

        # 悬停第 2 个词 → 内容应变化（回归点：旧实现 8 词共用 2 条提示）
        tgt = None
        for r in rects[1:]:
            if abs(r["y"] - rects[0]["y"]) > 2 or abs(r["x"] - rects[0]["x"]) > 30:
                tgt = r
                break
        tgt = tgt or rects[1]
        page.mouse.move(tgt["x"], tgt["y"])
        page.wait_for_timeout(900)
        c2 = page.evaluate(CARD_JS)
        res["card_text_2"] = (c2 or {}).get("text", "")
        res["card_content_differs"] = res["card_text_1"] != res["card_text_2"]

        # 点「认识了」
        clicked = page.evaluate("""() => {
          const h = document.getElementById('pae-hover-host');
          const b = h && h.shadowRoot ? h.shadowRoot.querySelector('.b') : null;
          if (b) { b.click(); return true; } return false;
        }""")
        res["click_known"] = clicked
        page.wait_for_timeout(1500)

        # 移到空白处 → 卡应消失
        page.mouse.move(20, 5)
        page.wait_for_timeout(900)
        c3 = page.evaluate(CARD_JS)
        res["card_hidden_after_leave"] = bool(c3 and c3["display"] == "none")
        ctx.close()
finally:
    eng.terminate()
    kill_port(PORT)
    subprocess.Popen([sys.executable, "-m", "uvicorn", "pae_core.api:app", "--port", str(PORT)],
                     cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# 查库：三证据是否真的落库
time.sleep(0.5)
try:
    conn = sqlite3.connect(str(db))
    rows = conn.execute("SELECT type, COUNT(*) FROM events GROUP BY type").fetchall()
    conn.close()
    res["event_counts"] = {t: n for t, n in rows}
except Exception as e:
    res["event_counts"] = {"error": str(e)[:120]}

ec = res.get("event_counts", {})
ok = (res.get("card_visible_1") and res.get("card_content_differs") and res.get("click_known")
      and res.get("card_hidden_after_leave") and ec.get("known_click", 0) >= 1 and ec.get("annotation_shown", 0) >= 1)
res["T7"] = "PASS" if ok else "FAIL"
(ART / "t7_hover.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(res, ensure_ascii=False, indent=1))
print("T7 HOVER:", res["T7"])