# -*- coding: utf-8 -*-
"""T10: 反馈闭环只读能力 —— metrics（噪声词浮出）/ context / word / page / decisions。
工件 tests/artifacts/t10_observe.json
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PORT = 4822  # 独立端口：不与浏览器测试抢 4815，避免端口竞争
BASE = "http://127.0.0.1:%d" % PORT
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


def call(path, body=None, method=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


res = {}
db = Path(tempfile.mkdtemp()) / "t10.db"
kill_port(PORT)
eng = start_engine(db)
try:
    assert wait_health(), "engine down"

    def ev(etype, lemma, page="p1", sess="s1"):
        return call("/v1/event", {"type": etype, "lemma": lemma, "page_id": page, "session_id": sess})

    # 噪声词 login：曝光 6 次（不同页面），零悬停零点击
    for i in range(6):
        ev("annotation_shown", "login", "nav-page-%d" % i)
    # 真实生词 contention：曝光 4 次 + 悬停 2 次（不同会话）+ 认识了 1 次
    for i in range(4):
        ev("annotation_shown", "contention", "doc-page-%d" % i)
    ev("hover", "contention", "doc-page-1", "s1")
    ev("hover", "contention", "doc-page-2", "s2")
    ev("known_click", "contention", "doc-page-1", "s1")
    # 中等词 quartz
    for i in range(3):
        ev("annotation_shown", "quartz", "doc-page-%d" % i)
    ev("hover", "quartz", "doc-page-3", "s3")

    st, m = call("/v1/cap/pae.metrics")
    body = m.get("result") or {}
    res["metrics_status"] = st
    res["annotation_exposure"] = body.get("annotation_exposure")
    res["hover_rate"] = body.get("hover_rate")
    res["click_rate"] = body.get("click_rate")
    res["top_ignored"] = [{"w": x["lemma"], "exp": x["exposure"], "ign": x["ignore_rate"]}
                          for x in (body.get("top_ignored_words") or [])[:3]]

    st2, ctx = call("/v1/cap/pae.context", {"top_n": 5})
    c = ctx.get("result") or {}
    res["context_status"] = st2
    res["context_has_persona_prompt"] = bool(((c.get("persona") or {}).get("prompt") or "").strip())
    res["context_band"] = (c.get("learner") or {}).get("band")
    res["context_words"] = len(c.get("words") or [])
    res["context_budget"] = c.get("budget_tokens")

    st3, w = call("/v1/cap/pae.word", {"lemma": "contention"})
    res["word"] = {k: (w.get("result") or {}).get(k) for k in ("lemma", "s_value", "band", "hover", "known_click")}

    st4, pg = call("/v1/cap/pae.page")
    res["page"] = {k: (pg.get("result") or {}).get(k) for k in ("url", "source")}

    st5, dec = call("/v1/cap/pae.audit")   # 调用审计（pae.decisions 已专用于决策日志）
    res["decisions_count"] = len(dec.get("result") or [])
finally:
    eng.terminate(); kill_port(PORT)

ign = res.get("top_ignored") or []
ok = (res.get("metrics_status") == 200 and ign and ign[0]["w"] == "login" and ign[0]["ign"] >= 0.9
      and res.get("context_status") == 200 and res.get("context_has_persona_prompt")
      and isinstance(res.get("context_words"), int) and res.get("word", {}).get("hover") == 2
      and res.get("word", {}).get("known_click") == 1 and res.get("page", {}).get("source") == "last_exposure"
      and res.get("decisions_count", 0) > 0)
res["T10"] = "PASS" if ok else "FAIL"
(ART / "t10_observe.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(res, ensure_ascii=False, indent=1))
print("T10 OBSERVE:", res["T10"])