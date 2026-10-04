# -*- coding: utf-8 -*-
"""T8: 注解密度 — 长文页面应远超 8 个注解（分块发送），且不超每页上限。工件 tests/artifacts/t8_density.json"""
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
EXT = str(ROOT / "extension")
PORT = 4815
ART = Path(__file__).parent / "artifacts"
ART.mkdir(exist_ok=True)

WORDS = ("contention scheduler intensified daemon stalled ephemeral payload recalcitrant "
         "arbitration idempotent throughput latency backpressure observable deterministic "
         "concurrency interleaving starvation livelock heuristic cardinality predicate "
         "monotonic idempotency serialization replication quorum consensus sharding "
         "checkpointing compaction vacuum fragmentation eviction thrashing locality").split()

paras = []
for i in range(30):
    chunk = WORDS[(i * 7) % len(WORDS):(i * 7) % len(WORDS) + 12] or WORDS[:12]
    paras.append("<p>Paragraph %d discusses %s in the context of modern database systems.</p>" % (i, " ".join(chunk)))
FIX = Path(__file__).parent / "fixture_longpage.html"
FIX.write_text("<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>" + "".join(paras) + "</body></html>",
               encoding="utf-8")


def kill_port(port):
    out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=20).stdout
    pids = {p.split()[4] for p in out.splitlines()
            if len(p.split()) >= 5 and p.split()[1].endswith(":" + str(port)) and p.split()[3] == "LISTENING"}
    for pid in pids:
        subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
    time.sleep(1)


def start_engine(db):
    env = dict(os.environ); env["PAE_DB_PATH"] = str(db)
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


res = {}
kill_port(PORT)
db = Path(tempfile.mkdtemp()) / "t8.db"
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
            tempfile.mkdtemp(prefix="pae-t8-"), headless=True, channel="chromium", timeout=60000,
            args=["--disable-extensions-except=" + EXT, "--load-extension=" + EXT])
        page = ctx.new_page()
        page.goto(FIX.as_uri())
        last = 0
        for _ in range(90):
            page.wait_for_timeout(1000)
            n = page.evaluate("CSS.highlights && CSS.highlights.get('pae-word') ? CSS.highlights.get('pae-word').size : 0")
            if n == last and n > 8:
                break
            last = n
        res["highlight_total"] = page.evaluate("CSS.highlights && CSS.highlights.get('pae-word') ? CSS.highlights.get('pae-word').size : 0")
        dbg = page.evaluate("document.documentElement.dataset.paeDebug || ''")
        try:
            res["debug"] = json.loads(dbg)
        except Exception:
            res["debug"] = {}
        res["page_text_chars"] = page.evaluate("document.body.textContent.length")
        ctx.close()
finally:
    eng.terminate(); kill_port(PORT)
    subprocess.Popen([sys.executable, "-m", "uvicorn", "pae_core.api:app", "--port", str(PORT)],
                     cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

n = res.get("highlight_total", 0)
res["T8"] = "PASS" if n > 8 and n <= 40 else "FAIL"
(ART / "t8_density.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps({k: res[k] for k in ("page_text_chars", "highlight_total", "T8")}, ensure_ascii=False))
print("T8 DENSITY:", res["T8"])