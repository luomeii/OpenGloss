"""Latency probe -- what it actually measures (honest naming, see audit R1-R3):

SEGMENT A (product path, HTTP): POST /v1/annotate with a 5000-WORD text. Measures the
  server HTTP round-trip of "tokenize 5000 words + account for the FIRST 8 annotated
  words". engine.py breaks out of the loop as soon as len(out) >= BUDGET (default 8),
  so the response never carries more than 8 annotations: this is NOT the cost of
  annotating a 5000-word page end-to-end. The annotation path is asserted to be LIVE
  on the fresh state (both warmups + the first timed run must return >= 1 annotation):
  a dead dictionary / empty annotation list FAILS instead of getting faster and passing.

SEGMENT B (isomorphic estimate, NOT the product path): DOM text extraction performed by
  a JS snippet owned by THIS TEST, evaluated on the 24-word fixture_m2.html page.
  It does not exercise extension content.js.

SEGMENT C (isomorphic estimate, NOT the product path): CSS.highlights.set performed by a
  JS snippet owned by THIS TEST. It does not exercise extension render code.

Acceptance: server_p95_ms < 50 AND (dom_extract + render) < 25 AND the annotation path
is provably live on the fresh state: BOTH warmups and the FIRST timed run returned
>= 1 annotation (a dead dictionary returns 0 and FAILS instead of getting faster).
The 20 timed runs re-post the same text, so the engine stops annotating words it has
already accounted for; annotated_runs / zero_annotation_runs / the two per-group p95
values report that drift instead of hiding it.
Artifact: tests/artifacts/e2e_latency.json
"""
import http.client
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
EXT = str(ROOT / "extension")
FIXTURE = Path(__file__).parent / "fixture_m2.html"
ART = Path(__file__).parent / "artifacts"
ART.mkdir(exist_ok=True)
PORT = 4815
TARGET_WORDS = 5000

# Mirrors engine.py:49 -- the per-request annotation budget that truncates segment A.
try:
    _cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
except Exception:
    _cfg = {}
BUDGET = int(os.environ.get("PAE_BUDGET", _cfg.get("budget", 8)))

MEASUREMENT = {
    "segment_a": ("5000 词输入的 HTTP 往返 + 前 %d 词记账（引擎 BUDGET 后 break；"
                  "annotations = 单次响应条数；同文本重放会因记账而递减到 0，如实计数）" % BUDGET),
    "segment_b": "同构操作估算：测试自带 DOM 文本抽取 JS（24 词 fixture），非扩展 content.js",
    "segment_c": "同构操作估算：测试自带 CSS.highlights.set JS，非扩展 render 路径",
    "product_render_path_measured": False,
    "annotation_budget": BUDGET,
    "target_words": TARGET_WORDS,
}

# The 8 known content words present in fixture_m2.html text nodes.
SURFACES = ["contention", "scheduler", "intensified", "worker",
            "threads", "considered", "alternatives", "complexity"]


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


def build_text():
    """Repeat the fixture's visible words until >= 5000 words (the segment A payload)."""
    html = FIXTURE.read_text(encoding="utf-8")
    body = re.sub(r"<[^>]+>", " ", html)
    words = re.findall(r"[A-Za-z][A-Za-z'\-]*", body)
    if not words:
        words = ["contention"]
    out = []
    n = 0
    while n < TARGET_WORDS:
        out.extend(words)
        n += len(words)
    return " ".join(out[:TARGET_WORDS])


def pct(values, p):
    s = sorted(values)
    if not s:
        return 0.0
    k = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
    return s[k]


def annotate_once(text, page_id):
    """One POST /v1/annotate; returns (elapsed_ms, annotation_count)."""
    body = json.dumps({"text": text, "page_id": page_id})
    headers = {"Content-Type": "application/json"}
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=120)
    t0 = time.perf_counter()
    c.request("POST", "/v1/annotate", body=body, headers=headers)
    raw = c.getresponse().read()
    t1 = time.perf_counter()
    c.close()
    n = -1
    try:
        parsed = json.loads(raw)
        n = len(parsed) if isinstance(parsed, list) else -1
    except Exception:
        n = -1
    return (t1 - t0) * 1000.0, n


def segment_a(text):
    """5000-word input HTTP round-trip + first-BUDGET accounting.

    2 warmups + 20 timed runs. The annotation path is asserted live on the fresh
    state (warmups and the first timed run must carry >= 1 annotation) -- a dead
    dictionary yields 0 and can no longer pass just by being faster.

    Re-posting the SAME text is not a stable annotation load: the engine records
    the shown words and stops annotating them once S crosses annotate_max_s, so
    the tail of the 20 runs legitimately returns 0 annotations. That drift is
    counted and reported (annotated_runs / zero_annotation_runs), not hidden.
    """
    warm = [annotate_once(text, "latency-a-warm-%d" % i) for i in range(2)]
    timed = [annotate_once(text, "latency-a-%d" % i) for i in range(20)]
    warm_anns = [n for _, n in warm]
    times = [t for t, _ in timed]
    anns = [n for _, n in timed]
    annotated_times = [t for t, n in timed if n > 0]
    zero_times = [t for t, n in timed if n == 0]
    return {
        "server_p50_ms": round(statistics.median(times), 2),
        "server_p95_ms": round(pct(times, 95), 2),
        "server_p95_ms_annotated_runs": round(pct(annotated_times, 95), 2),
        "server_p95_ms_zero_annotation_runs": round(pct(zero_times, 95), 2),
        "server_runs": times,
        "server_annotations_per_run": anns,
        "server_annotations_min": min(anns) if anns else -1,
        "server_annotations_max": max(anns) if anns else -1,
        "warmup_annotations": warm_anns,
        "first_response_annotations": warm_anns[0] if warm_anns else -1,
        "first_timed_annotations": anns[0] if anns else -1,
        "all_warmups_annotated": bool(warm_anns) and all(n > 0 for n in warm_anns),
        "annotated_runs": len(annotated_times),
        "zero_annotation_runs": len(zero_times),
        "annotation_budget": BUDGET,
        "annotation_drift_note": ("同文本重复请求会被引擎记账（annotation_shown 抬高 S），"
                                  "记账满后返回 0 条；上列 annotated_runs/zero_annotation_runs 如实计数"),
    }


DOM_JS = """() => {
  const root = document.body || document.documentElement;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
  const nodes = [];
  let words = 0;
  let n = walker.nextNode();
  while (n) {
    const t = (n.textContent || '').trim();
    if (t) {
      nodes.push(n);
      const m = t.match(/[A-Za-z][A-Za-z'-]*/g);
      if (m) words += m.length;
    }
    n = walker.nextNode();
  }
  return { nodes: nodes.length, words: words };
}"""

RENDER_JS = """(surfaces) => {
  const root = document.body || document.documentElement;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
  const nodes = [];
  let n = walker.nextNode();
  while (n) {
    if ((n.textContent || '').trim()) nodes.push(n);
    n = walker.nextNode();
  }
  const ranges = [];
  for (const surface of surfaces) {
    for (const node of nodes) {
      const idx = node.textContent.indexOf(surface);
      if (idx < 0) continue;
      const r = document.createRange();
      r.setStart(node, idx);
      r.setEnd(node, idx + surface.length);
      ranges.push(r);
      break;
    }
  }
  if (ranges.length) CSS.highlights.set('pae-word', new Highlight(...ranges));
  return { ranges: ranges.length };
}"""


def segment_bc(results):
    """Segments B/C: test-owned JS on the 24-word fixture -- isomorphic estimates only."""
    with sync_playwright() as p:
        profile = tempfile.mkdtemp(prefix="pae-lat-")
        ctx = p.chromium.launch_persistent_context(
            profile, headless=True, channel="chromium", timeout=60000,
            args=["--disable-extensions-except=" + EXT, "--load-extension=" + EXT],
        )
        page = ctx.new_page()
        page.goto(FIXTURE.as_uri())
        page.wait_for_timeout(3000)

        # Segment B: DOM extraction (test-owned JS, fixture page)
        dom_times = []
        dom_info = {}
        for _ in range(10):
            t0 = time.perf_counter()
            dom_info = page.evaluate(DOM_JS)
            t1 = time.perf_counter()
            dom_times.append((t1 - t0) * 1000.0)
        results["dom_nodes"] = dom_info.get("nodes")
        results["dom_words"] = dom_info.get("words")
        results["dom_extract_ms_estimate"] = round(statistics.mean(dom_times), 2)

        # Segment C: CSS Custom Highlight render (test-owned JS, fixture page)
        page.evaluate(RENDER_JS, SURFACES)  # warm
        render_times = []
        render_info = {}
        for _ in range(10):
            t0 = time.perf_counter()
            render_info = page.evaluate(RENDER_JS, SURFACES)
            t1 = time.perf_counter()
            render_times.append((t1 - t0) * 1000.0)
        results["render_ranges"] = render_info.get("ranges")
        results["render_ms_estimate"] = round(statistics.mean(render_times), 2)
        results["highlight_size"] = page.evaluate(
            "(() => { try { const h = CSS.highlights && CSS.highlights.get('pae-word');"
            " return h ? h.size : -1; } catch (e) { return -2; } })()")
        ctx.close()


def main():
    results = {}
    kill_port(PORT)
    tmpdb = Path(tempfile.mkdtemp(prefix="pae-latdb-")) / "lat.db"
    engine = start_engine(tmpdb)
    try:
        engine_ok = False
        for _attempt in range(3):
            if wait_engine_verified(PORT, tmpdb, engine, timeout=40):
                engine_ok = True
                break
            engine = start_engine(tmpdb)
        if not engine_ok:
            print(json.dumps({"engine_up": False}, ensure_ascii=False))
            print("E2E LATENCY: FAIL (engine did not start)")
            return 1
        text = build_text()
        results["text_words"] = len(text.split())
        results.update(segment_a(text))
        segment_bc(results)
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

    dom_est = results.get("dom_extract_ms_estimate", 1e9)
    render_est = results.get("render_ms_estimate", 1e9)
    p95 = results.get("server_p95_ms", 1e9)
    ann_min = results.get("server_annotations_min", -1)
    out = {
        "text_words": results.get("text_words"),
        "server_p50_ms": results.get("server_p50_ms"),
        "server_p95_ms": p95,
        "server_annotations_min": ann_min,
        "server_annotations_max": results.get("server_annotations_max"),
        "warmup_annotations": results.get("warmup_annotations"),
        "annotated_runs_of_20": results.get("annotated_runs"),
        "zero_annotation_runs_of_20": results.get("zero_annotation_runs"),
        "dom_extract_ms_estimate": results.get("dom_extract_ms_estimate"),
        "render_ms_estimate": results.get("render_ms_estimate"),
    }
    print("detail: " + json.dumps({
        "server_runs_ms": [round(x, 2) for x in results.get("server_runs", [])],
        "server_annotations_per_run": results.get("server_annotations_per_run"),
        "dom_nodes": results.get("dom_nodes"),
        "dom_words": results.get("dom_words"),
        "render_ranges": results.get("render_ranges"),
        "highlight_size": results.get("highlight_size"),
    }, ensure_ascii=False))
    print(json.dumps(out, ensure_ascii=False))

    warm_ok = results.get("all_warmups_annotated") is True
    first_ok = results.get("first_response_annotations", -1) > 0
    first_timed_ok = results.get("first_timed_annotations", -1) > 0
    ok = (p95 < 50
          and (dom_est + render_est) < 25
          and warm_ok and first_ok and first_timed_ok)
    results["measurement"] = MEASUREMENT
    results["run_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    results["E2E_LATENCY"] = "PASS" if ok else "FAIL"
    results["acceptance"] = {
        "server_p95_lt_50": p95 < 50,
        "dom_plus_render_estimate_lt_25": (dom_est + render_est) < 25,
        "warmup_responses_annotated_gt_0": warm_ok,
        "first_response_annotated_gt_0": first_ok,
        "first_timed_run_annotated_gt_0": first_timed_ok,
    }
    ART.joinpath("e2e_latency.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print("E2E LATENCY: %s | 实测=5000词输入HTTP往返+前%d词记账 p95=%.2fms 注解/轮=%s 首条=%s "
          "本轮注解轮数=%s/20 零注解轮数=%s | 段B/C=同构估算(非产品渲染路径) dom=%.2fms render=%.2fms"
          % (results["E2E_LATENCY"], BUDGET, p95,
             results.get("server_annotations_per_run"),
             results.get("first_response_annotations"),
             results.get("annotated_runs"), results.get("zero_annotation_runs"),
             dom_est, render_est))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
