"""T4 acceptance: (1) the extension forwards session_id to the engine;
(2) an HN-style <table> nav menu is excluded from extraction by the three-layer
filter, while body paragraphs still annotate.

Owns the engine lifecycle on 127.0.0.1:4815 with a temp DB (same pattern as
m2_acceptance.py). The production engine is restarted afterwards.
"""
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]          # .../pae
EXT = str(ROOT / "extension")
TESTS = Path(__file__).parent
FIXTURE = TESTS / "fixture_hn_menu.html"
ARTIFACT = TESTS / "artifacts" / "t4_filter.json"
PORT = 4815
CONTROL_PAGE = "ctl://menu-control"

# 顶部 table 菜单里的 5 个链接词（HN 风格：菜单是 <td> 里的 <a>，标签名 SKIP 抓不到）
MENU_WORDS = ["archive", "threads", "submissions", "login", "logout"]
MENU_LEMMAS = {"archive", "thread", "submission", "login", "logout"}
# 正文低频词（bnc > bnc_known_rank=3000，保证引擎会给注解）
CONTENT_WORDS = ["contention", "scheduler", "intensified", "ephemeral", "payload",
                 "recalcitrant", "perfunctory", "laconic", "dilapidated", "vociferous",
                 "garrulous", "obstreperous", "perspicacious", "truculent", "insouciant",
                 "languid", "daemon", "stalled"]

FIXTURE_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>PAE T4 HN menu fixture</title>
<style>
  html, body { margin: 0; padding: 0; }
  body { font: 14px/1.45 sans-serif; }
  table#hnmenu { width: 100%; border-collapse: collapse; }
  td.pagetop { background: #ff6600; padding: 3px 6px; }
  td.pagetop a { color: #000; text-decoration: none; margin-right: 8px; }
  #topgap { height: 150px; background: #f6f6ef; }
  article { padding: 8px; }
  p { margin: 0 0 12px 0; }
  #tailgap { height: 500px; background: #f6f6ef; }
</style>
</head>
<body>
<table id="hnmenu"><tbody><tr>
  <td class="pagetop">
    <span class="pagetop">
      <a href="#archive">archive</a>
      <a href="#threads">threads</a>
      <a href="#submissions">submissions</a>
      <a href="#login">login</a>
      <a href="#logout">logout</a>
    </span>
  </td>
</tr></tbody></table>
<div id="topgap"></div>
<article id="main">
<p id="p1">The contention on the scheduler lock intensified while the daemon stalled.</p>
<p id="p2">We examined the ephemeral payload before the recalcitrant inspector arrived.</p>
<p id="p3">A perfunctory glance at the laconic report revealed nothing unusual.</p>
<p id="p4">The dilapidated workshop exuded a vociferous clatter all afternoon.</p>
<p id="p5">Her garrulous neighbour proved obstreperous whenever the post arrived at dawn.</p>
<p id="p6">A perspicacious truculent observer remained insouciant and languid throughout.</p>
</article>
<div id="tailgap"></div>
</body></html>
"""

# 旧提取器（只有标签名 SKIP）会收哪些文本节点 —— 用来证明 fixture 的菜单本来会漏进来
LEGACY_NODES_JS = """(() => {
  var SKIP = 'script,style,code,pre,kbd,samp,textarea,input,button,select,nav,footer,header,aside';
  var root = document.body;
  if (!root) return [];
  var w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
  var n, out = [];
  while ((n = w.nextNode())) {
    var el = n.parentElement;
    if (!el || el.closest(SKIP)) continue;
    var t = (n.textContent || '').trim();
    if (t) out.push(t);
  }
  return out;
})()"""

# 主世界高亮表：取出每个 Range 实际覆盖的文本（= 真正被高亮的词）
HIGHLIGHT_JS = """(() => {
  try {
    var S = window.__paeHL;
    if (!S || !S.entries) return [];
    var out = [];
    for (var i = 0; i < S.entries.length; i++) {
      try {
        var r = S.entries[i].range;
        out.push(String(r.startContainer.textContent).slice(r.startOffset, r.endOffset));
      } catch (e) { out.push('?'); }
    }
    return out;
  } catch (e) { return ['ERR:' + e.message]; }
})()"""


def port_owner_pids(port):
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                             capture_output=True, text=True, errors='replace', timeout=20).stdout
    except Exception:
        return []
    pids = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[1].endswith(":" + str(port)) and parts[3] == "LISTENING":
            pids.add(parts[4])
    return sorted(pids)


def kill_port(port):
    for pid in port_owner_pids(port):
        try:
            subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True, timeout=20)
        except Exception:
            pass
    time.sleep(1)


def start_engine(db_path):
    env = dict(os.environ)
    env["PAE_DB_PATH"] = str(db_path)
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "pae_core.api:app", "--port", str(PORT)],
        cwd=str(ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


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


def wait_health(timeout=40):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/v1/health" % PORT, timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def read_debug(page):
    raw = page.evaluate("document.documentElement.dataset.paeDebug || ''")
    try:
        return json.loads(raw) if raw else {}
    except Exception:
        return {"_raw": raw[:200]}


def post_annotate(text, page_id, session_id=None):
    body = json.dumps({"text": text, "page_id": page_id,
                       "session_id": session_id or ""}).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:%d/v1/annotate" % PORT, data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode("utf-8"))


def run_browser_checks(results):
    errors = []
    with sync_playwright() as p:
        profile = tempfile.mkdtemp(prefix="pae-t4-")
        ctx = p.chromium.launch_persistent_context(
            profile, headless=True, channel="chromium", timeout=60000,
            args=["--disable-extensions-except=" + EXT, "--load-extension=" + EXT],
        )
        results["service_workers"] = len(ctx.service_workers)
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(FIXTURE.as_uri())

        dbg = {}
        for _ in range(60):   # 引擎冷启动加载词典 ~15s
            page.wait_for_timeout(1000)
            dbg = read_debug(page)
            if dbg.get("annotations", -1) not in (-1, None):
                break
        page.wait_for_timeout(1500)   # 等高亮注册完成

        results["debug"] = dbg
        results["legacy_nodes"] = page.evaluate(LEGACY_NODES_JS)
        results["highlights"] = page.evaluate(HIGHLIGHT_JS)
        results["highlight_ranges"] = page.evaluate(
            "(() => { try { const h = CSS.highlights && CSS.highlights.get('pae-word');"
            " return h ? h.size : -1; } catch (e) { return -2; } })()")
        results["page_errors"] = len(errors)
        results["errors"] = errors[:3]
        ctx.close()


def read_events(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT ts, type, payload FROM events ORDER BY ts, rowid").fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        try:
            payload = json.loads(r["payload"])
        except Exception:
            payload = {}
        out.append({"ts": r["ts"], "type": r["type"], "payload": payload})
    return out


def main():
    results = {"menu_words": MENU_WORDS, "content_words": CONTENT_WORDS}
    FIXTURE.write_text(FIXTURE_HTML, encoding="utf-8")
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)

    kill_port(PORT)
    tmpdb = Path(tempfile.mkdtemp(prefix="pae-t4-db-")) / "t4.db"
    results["db_path"] = str(tmpdb)
    engine = start_engine(tmpdb)
    try:
        results["engine_up"] = False
        for _attempt in range(3):
            if wait_engine_verified(PORT, tmpdb, engine, timeout=40):
                results["engine_up"] = True
                break
            engine = start_engine(tmpdb)
        if not results["engine_up"]:
            results["verdict"] = "FAIL"
            ARTIFACT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
            print(json.dumps(results, ensure_ascii=False, indent=1))
            print("T4 ACCEPTANCE: FAIL (engine did not start)")
            return 1
        run_browser_checks(results)

        events = read_events(tmpdb)
        ext = [e for e in events
               if e["type"] == "encounter"
               and (e["payload"].get("page_id") or "") != CONTROL_PAGE]
        results["ext_event_count"] = len(ext)
        results["ext_surfaces"] = [p.get("surface") for p in
                                   (e["payload"] for e in ext)]
        results["ext_lemmas"] = [p.get("lemma") for p in (e["payload"] for e in ext)]
        results["ext_session_ids"] = [p.get("session_id") for p in (e["payload"] for e in ext)]

        # 非空性护栏：菜单词本身可被引擎注解（否则「菜单词没出现」是空断言）
        control = post_annotate(" ".join(MENU_WORDS), CONTROL_PAGE, "s-ctl")
        results["control_surfaces"] = [a.get("surface") for a in control]
    finally:
        try:
            engine.terminate()
        except Exception:
            pass
        kill_port(PORT)
        subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "pae_core.api:app", "--port", str(PORT)],
            cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    surfaces = [str(s or "").lower() for s in results.get("ext_surfaces", [])]
    lemmas = [str(s or "").lower() for s in results.get("ext_lemmas", [])]
    hl = [str(s or "").lower() for s in results.get("highlights", [])]
    legacy = [str(s or "").lower() for s in results.get("legacy_nodes", [])]
    menu_set = set(MENU_WORDS)

    results["menu_in_events"] = sorted(set(s for s in surfaces if s in menu_set)
                                       | set(s for s in lemmas if s in MENU_LEMMAS))
    results["content_in_events"] = sorted(set(s for s in surfaces if s in set(CONTENT_WORDS)))
    results["menu_in_highlights"] = sorted(set(s for s in hl if s in menu_set))
    results["content_in_highlights"] = sorted(set(s for s in hl if s in set(CONTENT_WORDS)))
    results["menu_in_legacy_nodes"] = sorted(set(s for s in legacy if s in menu_set))
    sids = [s for s in results.get("ext_session_ids", []) if s]
    results["session_ids_non_null"] = sorted(set(sids))
    results["session_id_ok"] = bool(sids) and all(re.match(r"^s[a-z0-9]+$", str(s)) for s in sids)

    checks = {
        "engine_up": results.get("engine_up") is True,
        "ext_annotations_present": len(results.get("ext_surfaces", [])) > 0,
        "menu_absent_from_events": results["menu_in_events"] == [],
        "menu_absent_from_highlights": results["menu_in_highlights"] == [],
        "content_present_in_events": len(results["content_in_events"]) >= 3,
        "content_present_in_highlights": len(results["content_in_highlights"]) >= 1,
        "session_id_recorded": results["session_id_ok"],
        "control_menu_annotatable": len(results.get("control_surfaces", [])) >= 1,
        "fixture_menu_would_leak": set(results["menu_in_legacy_nodes"]) == set(MENU_WORDS),
        "no_page_errors": results.get("page_errors", 1) == 0,
    }
    results["checks"] = checks
    results["verdict"] = "PASS" if all(checks.values()) else "FAIL"
    ARTIFACT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")

    print(json.dumps({k: results[k] for k in (
        "verdict", "checks", "menu_in_events", "menu_in_highlights", "menu_in_legacy_nodes",
        "content_in_events", "content_in_highlights", "session_ids_non_null",
        "control_surfaces", "ext_event_count", "rendered") if k in results}, ensure_ascii=False, indent=1))
    print("T4 ACCEPTANCE:", results["verdict"])
    print("artifact:", ARTIFACT)
    return 0 if results["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
