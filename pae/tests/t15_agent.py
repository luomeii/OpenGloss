# -*- coding: utf-8 -*-
"""T15: 智能体循环 —— 内置角色真的会「观察→判断→行动」，且行动走同一条能力面（dogfooding）。

需要 LLM 与代理（api.deepseek.com）。工件 tests/artifacts/t15_agent.json
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
PORT = 4825
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


def cap(name, body=None):
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(BASE + "/v1/cap/" + name, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


res = {}
db = Path(tempfile.mkdtemp()) / "t15.db"
kill_port(PORT)
eng = start_engine(db)
try:
    assert wait_health(), "engine down"
    # 造点内容，让它有东西可看
    for i in range(4):
        cap("pae.annotate", {"text": "contention scheduler quartz ubiquitous tenacious",
                             "page_id": "seed-%d" % i, "session_id": "s1"})
    cap("pae.event", {"type": "annotation_shown", "lemma": "login", "page_id": "nav-1"})

    st1, r1 = cap("pae.agent.tick", {"stimulus": "用户抱怨页面上注解太多了，你看看要不要做点什么"})
    tick1 = r1.get("result") or {}
    res["tick1_status"] = st1
    res["tick1_thought"] = tick1.get("thought")
    res["tick1_actions"] = [a.get("name") for a in (tick1.get("actions") or [])]
    res["tick1_action_results"] = [{"name": a.get("name"), "status": a.get("status"),
                                    "body": a.get("body")} for a in (tick1.get("actions") or [])]
    res["tick1_error"] = tick1.get("error")
    res["tick1_elapsed_ms"] = tick1.get("elapsed_ms")

    time.sleep(16)  # 等过 tick 限流窗口（15s）
    st2, r2 = cap("pae.agent.tick", {"stimulus": "现在是深夜，他刚关掉页面，没有新情况"})
    tick2 = r2.get("result") or {}
    res["tick2_thought"] = tick2.get("thought")
    res["tick2_actions"] = [a.get("name") for a in (tick2.get("actions") or [])]

    st3, lg = cap("pae.agent.log", {"n": 10})
    entries = lg.get("result") or []
    res["agent_log_rows"] = len(entries)
    res["logged_thought_nonempty"] = sum(1 for e in entries if (e.get("thought") or "").strip())

    # dogfooding：它执行的动作必须出现在审计里，且 actor=companion
    st4, dc = cap("pae.decisions", {"n": 20})
    dec = dc.get("result") or []
    res["decisions_by_companion"] = sum(1 for d in dec if d.get("actor") == "companion")
    st5, lg2 = cap("pae.push.log", {"n": 20})
    res["push_rows"] = len(lg2.get("result") or [])
    # 调用审计（含被门控拦下的尝试）——dogfooding 的直接证据
    st6, au = cap("pae.audit", {"actor": "companion", "n": 20})
    res["audit_by_companion"] = len(au.get("result") or [])
    res["audit_capabilities"] = sorted({x.get("capability") for x in (au.get("result") or [])})
finally:
    eng.terminate(); kill_port(PORT)

# 断言：循环跑通（思考非空、无 JSON 错误、留痕两条）；
# 且**它做的任何行动都必须走能力面并留下审计**（门控可以拒，但审计必须有——这才是 dogfooding 的证据）
acted = bool(res.get("tick1_actions")) or bool(res.get("tick2_actions"))
ok = (res.get("tick1_status") == 200 and (res.get("tick1_thought") or "").strip()
      and res.get("tick1_error") is None and res.get("agent_log_rows", 0) >= 2
      and res.get("logged_thought_nonempty", 0) >= 2
      and res.get("audit_by_companion", 0) >= 1
      and (not acted or res.get("audit_by_companion", 0) >= 2))
res["acted"] = acted
res["T15"] = "PASS" if ok else "FAIL"
(ART / "t15_agent.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(res, ensure_ascii=False, indent=1))
print("T15 AGENT LOOP:", res["T15"])