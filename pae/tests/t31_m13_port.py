# -*- coding: utf-8 -*-
"""M13: 基准 demo 六组件移植到扩展角色侧栏（Shadow DOM）——真浏览器验证。

六组件：三段卡 .card / 掌握度圆环+delta .ring+.delta / 词行 .rows>.row / 印章卡 .stamp /
三档分色 ::highlight(pae-word-new|mid|high) / 手动换肤 .theme-btn。
另验：页面 DOM 零污染（body.children 前后一致）、系统偏好=初始值、手动覆盖生效。

截图工件：tests/artifacts/（跑完自动落在测试工件目录，不随仓库分发）
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
OUT = Path(__file__).resolve().parent / "artifacts"
OUT.mkdir(parents=True, exist_ok=True)
FIX = ROOT / "tests" / "fixture_form.html"
SHOT = OUT / "M13-移植后.png"
SHOT_NIGHT = OUT / "M13-移植后-暗夜.png"


def kill_port(port):
    out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=20).stdout
    pids = {p.split()[4] for p in out.splitlines()
            if len(p.split()) >= 5 and p.split()[1].endswith(":" + str(port)) and p.split()[3] == "LISTENING"}
    for pid in pids:
        subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
    time.sleep(1)


def wait_engine_verified(port, db_path, eng=None, tries=3, timeout=90):
    for attempt in range(tries):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/v1/health" % port, timeout=3) as r:
                    h = json.loads(r.read().decode("utf-8"))
                if str(h.get("db")).replace(chr(92), "/") == str(db_path).replace(chr(92), "/"):
                    return True
                break
            except Exception:
                time.sleep(0.5)
        kill_port(port)
        if eng is not None and attempt < tries - 1:
            try:
                eng.terminate()
            except Exception:
                pass
            return False
    raise RuntimeError("engine identity check failed on port %d" % port)


def post(path, body=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body or {}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))


res = {}
tmpd = Path(tempfile.mkdtemp())
kill_port(PORT)
env = dict(os.environ)
env["PAE_DB_PATH"] = str(tmpd / "m13.db")
eng = subprocess.Popen([sys.executable, "-m", "uvicorn", "pae_core.api:app", "--port", str(PORT)],
                       cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _attempt in range(3):
        if wait_engine_verified(PORT, tmpd / "m13.db", eng):
            break
        eng = subprocess.Popen([sys.executable, "-m", "uvicorn", "pae_core.api:app", "--port", str(PORT)],
                               cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        raise RuntimeError("engine start failed")

    # ---- 三档播种：给 fixture 里会出现的词先攒证据（S 越高档位越高），不预先 annotate ----
    # buffer 14 次 hover -> S≈0.60 快出带；pool 5 -> ≈0.37 带中；eviction 2 -> ≈0.27 带中；其余新词
    seed = {"buffer": 14, "pool": 5, "eviction": 2}
    for w, n in seed.items():
        for i in range(n):
            post("/v1/event", {"type": "hover", "lemma": w, "surface": w,
                               "page_id": "seed%d" % i, "session_id": "s%d" % i,
                               "sentence": "seed %d %s" % (i, w)})
    res["seed"] = seed

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            tempfile.mkdtemp(prefix="pae-m13-"), headless=True, channel="chromium", timeout=60000,
            color_scheme="dark",
            args=["--disable-extensions-except=" + EXT, "--load-extension=" + EXT])
        page = ctx.new_page()
        page.goto(FIX.as_uri())
        res["body_children_before"] = page.evaluate("document.body.children.length")

        hl = 0
        for _ in range(70):
            page.wait_for_timeout(1000)
            hl = page.evaluate("CSS.highlights && CSS.highlights.get('pae-word') ? CSS.highlights.get('pae-word').size : 0")
            if page.evaluate("!!document.getElementById('pae-char-host')") and hl > 0:
                break
        res["host_present"] = page.evaluate("!!document.getElementById('pae-char-host')")
        res["shadow_ok"] = page.evaluate("(h => !!(h && h.shadowRoot))(document.getElementById('pae-char-host'))")
        res["pae_word_ranges"] = hl
        res["body_children_after_annotate"] = page.evaluate("document.body.children.length")

        # ---- 换肤：系统偏好(dark) 作为初始值 ----
        res["system_dark"] = page.evaluate("window.matchMedia('(prefers-color-scheme: dark)').matches")
        res["theme_night_initial"] = page.evaluate(
            "document.getElementById('pae-char-host').shadowRoot.getElementById('paeroot').classList.contains('theme-night')")
        res["side_bg_initial"] = page.evaluate(
            "getComputedStyle(document.getElementById('pae-char-host').shadowRoot.getElementById('side')).backgroundImage.slice(0,42)")

        # ---- 打开侧栏（自动拉一次统计）→ 再点两次「刷新」让 delta 有真实前后差 ----
        page.evaluate("document.getElementById('pae-char-host').shadowRoot.getElementById('sidebtn').click()")
        page.wait_for_timeout(4000)
        for _ in range(2):
            page.evaluate("document.getElementById('pae-char-host').shadowRoot.getElementById('sidereload').click()")
            page.wait_for_timeout(3500)
        res["side_display"] = page.evaluate(
            "getComputedStyle(document.getElementById('pae-char-host').shadowRoot.getElementById('side')).display")
        res["sidebd_text_len"] = page.evaluate(
            "document.getElementById('pae-char-host').shadowRoot.getElementById('sidebd').innerText.length")
        res["sidestats_text"] = page.evaluate(
            "document.getElementById('pae-char-host').shadowRoot.getElementById('sidestats').innerText")[:120]

        counts_night = page.evaluate("""() => {
          const sr = document.getElementById('pae-char-host').shadowRoot;
          const c = s => sr.querySelectorAll(s).length;
          return {card:c('.card'), ring:c('.ring'), rows:c('.rows'), stamp:c('.stamp'), themeBtn:c('.theme-btn'),
                  row:c('.row'), delta:c('.delta'), ch:c('.card .ch'), cb:c('.card .cb'), ca:c('.card .ca'),
                  ringSm:c('.ring.sm'), stampTagged:c('.stamp[data-tag]'),
                  exists:{sidewords:!!sr.getElementById('sidewords'), sidestamps:!!sr.getElementById('sidestamps'),
                          mute:!!sr.getElementById('mute'), sidein:!!sr.getElementById('sidein'),
                          margin:!!sr.getElementById('margin'), cap:!!sr.getElementById('cap')}};
        }""")
        res["counts"] = counts_night
        page.screenshot(path=str(SHOT_NIGHT))

        # ---- 手动换肤 → 羊皮纸（覆盖系统偏好），截图存档 ----
        page.evaluate("document.getElementById('pae-char-host').shadowRoot.getElementById('themebtn').click()")
        page.wait_for_timeout(500)
        res["theme_night_after_click"] = page.evaluate(
            "document.getElementById('pae-char-host').shadowRoot.getElementById('paeroot').classList.contains('theme-night')")
        res["side_bg_after_click"] = page.evaluate(
            "getComputedStyle(document.getElementById('pae-char-host').shadowRoot.getElementById('side')).backgroundImage.slice(0,42)")
        page.screenshot(path=str(SHOT))

        # ---- 三档高亮：注册名 / 条目数 / 样式表三条规则 / 分桶自洽 ----
        hlall = page.evaluate("""() => {
          const sz = n => { const h = (window.CSS && CSS.highlights) ? CSS.highlights.get(n) : null; return h ? h.size : -1; };
          const st = document.getElementById('pae-highlight-style');
          const txt = st ? st.textContent : '';
          const S = window.__paeHL || {meta:{}};
          const b = {new:0, mid:0, high:0};
          for (const k in (S.meta || {})) {
            const v = S.meta[k].s_value;
            b[(typeof v !== 'number') ? 'new' : (v < 0.2 ? 'new' : (v < 0.45 ? 'mid' : 'high'))]++;
          }
          return {keys: (window.CSS && CSS.highlights) ? Array.from(CSS.highlights.keys()) : [],
                  size_new: sz('pae-word-new'), size_mid: sz('pae-word-mid'), size_high: sz('pae-word-high'),
                  size_word: sz('pae-word'),
                  rule_new: txt.indexOf('::highlight(pae-word-new)') >= 0,
                  rule_mid: txt.indexOf('::highlight(pae-word-mid)') >= 0,
                  rule_high: txt.indexOf('::highlight(pae-word-high)') >= 0,
                  rule_legacy: txt.indexOf('::highlight(pae-word)') >= 0,
                  bands_meta: b};
        }""")
        res["highlights"] = hlall

        # ---- 零污染：body.children 数量与加载前一致 ----
        res["body_children_final"] = page.evaluate("document.body.children.length")
        res["body_tags_final"] = page.evaluate("Array.from(document.body.children).map(e=>e.tagName+'#'+e.id).join(',')")
        res["host_parent"] = page.evaluate("document.getElementById('pae-char-host').parentElement.tagName")
        ctx.close()
finally:
    eng.terminate()
    kill_port(PORT)

h = res.get("highlights") or {}
c = res.get("counts") or {}
checks = {
    "node_check": True,
    "six_components": all(c.get(k, 0) >= 1 for k in ("card", "ring", "rows", "stamp", "themeBtn")),
    "card_three_parts": c.get("ch", 0) >= 1 and c.get("cb", 0) >= 1 and c.get("ca", 0) >= 1,
    "delta_present": c.get("delta", 0) >= 1,
    "highlight_keys": all(n in (h.get("keys") or []) for n in ("pae-word-new", "pae-word-mid", "pae-word-high")),
    "highlight_rules_3": bool(h.get("rule_new") and h.get("rule_mid") and h.get("rule_high") and h.get("rule_legacy")),
    "highlight_partition": (h.get("size_new", -1) + h.get("size_mid", -1) + h.get("size_high", -1)) == h.get("size_word"),
    "three_bands_visible": all((h.get("bands_meta") or {}).get(k, 0) >= 1 for k in ("new", "mid", "high")),
    "system_pref_initial": bool(res.get("system_dark") and res.get("theme_night_initial")),
    "manual_override": res.get("theme_night_after_click") is False,
    "no_page_dom_change": res.get("body_children_before") == res.get("body_children_final") == res.get("body_children_after_annotate"),
    "existing_ui_alive": (res.get("side_display") == "flex" and (res.get("sidebd_text_len") or 0) > 0
                          and (res.get("sidestats_text") or "").find("转化率") >= 0),
}
res["checks"] = checks
res["M13"] = "PASS" if all(checks.values()) else "FAIL"
Path(ROOT / "tests" / "artifacts" / "t31_m13.json").write_text(
    json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(res, ensure_ascii=False, indent=1))
print("M13 PORT:", res["M13"])
