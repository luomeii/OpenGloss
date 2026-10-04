# -*- coding: utf-8 -*-
"""T12: 记忆三表 —— 写入守卫 / 淘汰 / 不可物理删除 / **关引擎重启后仍能取回**。工件 tests/artifacts/t12_memory.json"""
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
PORT = 4823  # 独立端口：不与浏览器测试抢 4815，避免端口竞争
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


def call(name, body=None):
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(BASE + "/v1/cap/" + name, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


res = {}
db = Path(tempfile.mkdtemp()) / "t12.db"
kill_port(PORT)
eng = start_engine(db)
try:
    assert wait_health(), "engine down"

    # 1) 写入守卫
    res["no_evidence"] = call("pae.remember", {"kind": "goal", "text": "他想把英语变成阅读副产品"})[0]
    res["bad_kind"] = call("pae.remember", {"kind": "nonsense", "text": "x", "evidence": ["e1"]})[0]
    res["too_long"] = call("pae.remember", {"kind": "goal", "text": "x" * 250, "evidence": ["e1"]})[0]

    # 2) 正常写入 9 条 → 第 9 条应触发淘汰（active 上限 8；2026-10-03 M16 从 5 提到 8）
    ids = []
    for i in range(9):
        st, b = call("pae.remember", {"kind": "preference", "text": "偏好第%d条" % i,
                                      "evidence": ["evt_%d" % i], "confidence": 0.5 + i * 0.05})
        ids.append(((b.get("result") or {}).get("fact_id"), (b.get("result") or {}).get("evicted")))
    st, act = call("pae.memory.search", {"query": "偏好", "k": 20})
    act_list = act.get("result") or []
    res["written"] = len(ids)
    res["active_after_9"] = len(act_list)
    res["evicted_present"] = any(e for _, e in ids)

    # 3) forget 不物理删除
    target = act_list[0]["fact_id"] if act_list else None
    st, fg = call("pae.forget", {"fact_id": target})
    res["forget_ok"] = (fg.get("result") or {}).get("changed")
    st, act2 = call("pae.memory.search", {"query": "偏好", "k": 20})
    res["active_after_forget"] = len(act2.get("result") or [])

    # 4) 会话 + 三轮对话
    st, s = call("pae.session.open", {"actor": "companion"})
    sid = (s.get("result") or {}).get("session_id")
    for role, txt in (("user", "contention 和 conflict 啥区别"), ("agent", "一个是抢资源，一个是两边对不上"),
                      ("user", "懂了")):
        call("pae.session.turn", {"session_id": sid, "role": role, "text": txt})
    st, rc = call("pae.memory.recent", {"session_id": sid, "n": 10})
    res["turns_before_restart"] = len(rc.get("result") or [])
    call("pae.session.close", {"session_id": sid, "summary": "讲了 contention vs conflict"})
    res["session_id"] = sid
finally:
    eng.terminate(); kill_port(PORT)

# 5) 关键：**重启引擎**（同一个库），记忆必须还在
eng2 = start_engine(db)
try:
    assert wait_health(), "engine restart failed"
    st, rc2 = call("pae.memory.recent", {"session_id": res.get("session_id"), "n": 10})
    res["turns_after_restart"] = len(rc2.get("result") or [])
    res["restart_first_turn"] = ((rc2.get("result") or [{}])[0] or {}).get("text")
    st, act3 = call("pae.memory.search", {"query": "偏好", "k": 20})
    res["active_after_restart"] = len(act3.get("result") or [])
finally:
    eng2.terminate(); kill_port(PORT)

ok = (res.get("no_evidence") == 500 and res.get("bad_kind") == 500 and res.get("too_long") == 500
      and res.get("active_after_9") == 8 and res.get("evicted_present")
      and res.get("active_after_forget") == 7
      and res.get("turns_before_restart") == 3 and res.get("turns_after_restart") == 3
      and res.get("active_after_restart") == 7)
res["T12"] = "PASS" if ok else "FAIL"
(ART / "t12_memory.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(res, ensure_ascii=False, indent=1))
print("T12 MEMORY:", res["T12"])