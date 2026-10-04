"""M2 acceptance (deterministic): fresh engine + fresh DB, headless Chromium with the
unpacked extension, verifies annotations actually render via CSS Custom Highlight API.

The test owns the engine lifecycle on 127.0.0.1:4815 with a temp DB so results do not
depend on accumulated production state. The production engine is restarted afterwards.

Acceptance:
  (1) annotations returned and rendered > 0;
  (2) BODY TEXT UNCHANGED -- baseline is captured by an init script BEFORE the first
      annotation exists (asserted at capture time from document.dataset.paeDebug),
      then compared against the post-annotation read; additionally the post-annotation
      text is compared against the pristine fixture text (hash equality) and the
      #main DOM shape (text nodes / elements) is required to be unchanged;
  (3) dynamically injected paragraph gets annotated;
  (4) zero page errors.
Artifact: tests/artifacts/m2_acceptance.json
"""
import hashlib
import html as html_mod
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]          # .../pae
PROJECT = ROOT.parent                                # 仓库根目录
EXT = str(ROOT / "extension")
FIXTURE = Path(__file__).parent / "fixture_m2.html"
ART = Path(__file__).parent / "artifacts"
ART.mkdir(exist_ok=True)
PORT = 4815

# The 8 known content words present in fixture_m2.html text nodes.
SURFACES = ["contention", "scheduler", "intensified", "worker",
            "threads", "considered", "alternatives", "complexity"]

FIXTURE.write_text("""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>PAE M2 fixture</title></head>
<body>
<article id="main">
<p id="p1">The contention on the scheduler lock intensified as worker threads multiplied.</p>
<p id="p2">We considered lock-free alternatives but the complexity outweighed the benefits.</p>
</article>
<div id="inject"></div>
</body></html>""", encoding="utf-8")


def norm_ws(s):
    """Collapse all whitespace: textContent keeps source indentation/newlines."""
    return re.sub(r"\s+", " ", s or "").strip()


def sha(s):
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()


def pristine_body_text():
    """Pre-annotation ground truth: the fixture's own visible body text (no extension ran)."""
    raw = FIXTURE.read_text(encoding="utf-8")
    body = raw.split("<body>", 1)[1].split("</body>", 1)[0]
    body = re.sub(r"<script.*?</script>", " ", body, flags=re.S | re.I)
    body = re.sub(r"<[^>]+>", " ", body)
    return norm_ws(html_mod.unescape(body))


def port_owner_pids(port):
    """PIDs listening on the port (Windows netstat parse)."""
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


def start_engine(db_path, extra_env=None):
    env = dict(os.environ)
    env["PAE_DB_PATH"] = str(db_path)
    if extra_env:
        env.update(extra_env)
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


def wait_health(timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/v1/health", timeout=3) as r:
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


# Runs at document_start, before any content script can touch the page. Captures the
# pristine body text at DOMContentLoaded AND the annotation count reported by the page
# at that exact moment, so the baseline is provably pre-annotation.
INIT_SNAPSHOT_JS = """
window.__paeTextSnapshot = null;
window.__paeTextSnapshotAnn = null;
(function () {
  function grab() {
    try {
      window.__paeTextSnapshot = document.body ? document.body.textContent : null;
      var d = document.documentElement.dataset.paeDebug || '';
      var n = -1;
      if (d) { try { var o = JSON.parse(d); n = (o && o.annotations != null) ? o.annotations : -1; } catch (e) { n = -2; } }
      window.__paeTextSnapshotAnn = n;
    } catch (e) { window.__paeTextSnapshot = null; }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', grab, { once: true });
  else grab();
})();
"""

# Post-annotation reads: normalized body text + #main DOM shape (text nodes / elements).
AFTER_JS = """() => {
  const main = document.getElementById('main');
  let nodes = -1, elems = -1;
  if (main) {
    const w = document.createTreeWalker(main, NodeFilter.SHOW_TEXT, null);
    nodes = 0;
    let x = w.nextNode();
    while (x) { if ((x.textContent || '').trim()) nodes++; x = w.nextNode(); }
    elems = main.querySelectorAll('*').length;
  }
  return {
    text: (document.body ? document.body.textContent : '').replace(/\\s+/g, ' ').trim(),
    main_text_nodes: nodes,
    main_elements: elems,
  };
}"""


def run_browser_checks(results, pristine):
    errors = []
    with sync_playwright() as p:
        profile = tempfile.mkdtemp(prefix="pae-m2-")
        ctx = p.chromium.launch_persistent_context(
            profile, headless=True, channel="chromium", timeout=60000,
            args=["--disable-extensions-except=" + EXT, "--load-extension=" + EXT],
        )
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script(INIT_SNAPSHOT_JS)
        page.goto(FIXTURE.as_uri())
        # SW 注册竞态修正（2026-10-02）：MV3 的 SW 要到首个导航后才激活，
        # 在 launch 后立即计数恒为 0（此前几十轮侥幸通过）。意图不变：确认 SW 存在。
        results["service_workers"] = len(ctx.service_workers)

        # 轮询等待注解到位（引擎首请求冷启动加载 77 万词典约 15s，固定 5s 不够）
        dbg = {}
        snap = None
        snap_ann = None
        for _ in range(90):   # 冷启动（77万词典）在串行跑时可能超过 40s；40→90 消除抖动
            page.wait_for_timeout(1000)
            dbg = read_debug(page)
            if snap is None:
                snap = page.evaluate("window.__paeTextSnapshot")
                snap_ann = page.evaluate("window.__paeTextSnapshotAnn")
            if dbg.get("annotations", -1) not in (-1, None):
                break

        dbg = read_debug(page)
        results["debug_first_batch"] = dbg
        results["annotations_returned"] = dbg.get("annotations", -1)
        results["rendered"] = (dbg.get("rendered") or {}).get("rendered", -1)
        # highlight registry must be queried from the MAIN world (the isolated content-
        # script world sees its own view; rendered ranges live in the document registry).
        results["highlight_ranges"] = page.evaluate(
            "(() => { try { const h = CSS.highlights && CSS.highlights.get('pae-word');"
            " return h ? h.size : -1; } catch (e) { return -2; } })()")

        # ---- R5 修复：基线来自 document_start 快照，且采集时刻页面自称 annotations<=0 ----
        after = page.evaluate(AFTER_JS)
        text_after = after.get("text", "")
        results["baseline_source"] = "init-script @DOMContentLoaded (before first annotation)"
        results["snapshot_captured"] = snap is not None
        results["snapshot_annotations_at_capture"] = snap_ann
        results["snapshot_taken_before_annotation"] = (snap is not None
                                                       and snap_ann in (-1, 0, None))
        results["snapshot_raw_len"] = len(snap or "")
        results["snapshot_norm_len"] = len(norm_ws(snap))
        results["after_norm_len"] = len(text_after)
        results["pristine_norm_len"] = len(pristine)
        results["lengths_note"] = "norm_* 为空白折叠后长度（比较口径）；raw 保留原始 textContent 长度"
        results["snapshot_sha256"] = sha(norm_ws(snap))
        results["after_sha256"] = sha(text_after)
        results["pristine_sha256"] = sha(pristine)
        results["early_snapshot_equals_after"] = bool(snap is not None
                                                      and norm_ws(snap) == text_after)
        results["pristine_text_matches"] = (text_after == pristine)
        results["surfaces_all_present"] = all(s in text_after for s in SURFACES)
        results["surfaces_found"] = [s for s in SURFACES if s in text_after]
        results["main_text_nodes"] = after.get("main_text_nodes")
        results["main_elements"] = after.get("main_elements")
        results["dom_shape_unchanged"] = (after.get("main_text_nodes") == 2
                                          and after.get("main_elements") == 2)
        # 合成断言：四条独立证据全真才叫「文本不变」
        results["text_unchanged"] = bool(
            results["snapshot_taken_before_annotation"]
            and results["early_snapshot_equals_after"]
            and results["pristine_text_matches"]
            and results["surfaces_all_present"]
            and results["dom_shape_unchanged"])
        results["text_unchanged_evidence"] = {
            "pre_annotation_snapshot": results["snapshot_taken_before_annotation"],
            "snapshot_eq_after": results["early_snapshot_equals_after"],
            "equals_pristine_fixture": results["pristine_text_matches"],
            "all_8_surfaces_present": results["surfaces_all_present"],
            "dom_shape_2_text_nodes_2_elements": results["dom_shape_unchanged"],
        }

        js_inject = ''.join([
            "document.getElementById('inject').innerHTML = ",
            '\"<p id=1>We must obfuscate the ephemeral laconic artefacts ',
            'before the perfunctory recalcitrant inspection.</p>\";',
        ]).replace('id=1', chr(39) + 'p3' + chr(39))
        page.evaluate(js_inject)
        page.wait_for_timeout(4000)
        dbg2 = read_debug(page)
        results["debug_second_batch"] = dbg2
        results["after_inject"] = dbg2.get("annotations", -1)
        results["after_inject_rendered"] = (dbg2.get("rendered") or {}).get("rendered", -1)
        results["page_errors"] = len(errors)
        results["errors"] = errors[:3]
        ctx.close()


def main():
    results = {"baseline_source": "unknown"}
    pristine = pristine_body_text()
    kill_port(PORT)
    tmpdb = Path(tempfile.mkdtemp(prefix="pae-db-")) / "m2.db"
    engine = start_engine(tmpdb)
    try:
        results["engine_up"] = False
        for _attempt in range(3):
            if wait_engine_verified(PORT, tmpdb, engine, timeout=40):
                results["engine_up"] = True
                break
            engine = start_engine(tmpdb)
        if not results["engine_up"]:
            print(json.dumps(results, ensure_ascii=False, indent=1))
            print("M2 ACCEPTANCE: FAIL (engine did not start)")
            return 1
        run_browser_checks(results, pristine)
    finally:
        try:
            engine.terminate()
        except Exception:
            pass
        kill_port(PORT)
        # restore the production engine (default DB)
        subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "pae_core.api:app", "--port", str(PORT)],
            cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    ok = (results.get("service_workers", 0) >= 1
          and results.get("annotations_returned", 0) > 0
          and results.get("rendered", 0) > 0
          and results.get("highlight_ranges", 0) > 0
          and results.get("text_unchanged") is True
          and results.get("page_errors", 1) == 0
          and results.get("after_inject", 0) > 0)
    results["run_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    results["M2"] = "PASS" if ok else "FAIL"
    print(json.dumps(results, ensure_ascii=False, indent=1))
    print("M2 ACCEPTANCE:", results["M2"])
    (ART / "m2_acceptance.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
