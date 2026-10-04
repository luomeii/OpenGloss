# -*- coding: utf-8 -*-
"""T17: 权限空间校准 —— 既不能太小（压抑智能），也不能太大（翻车）。

验证原则：AI 能收紧自己不能放宽自己；能影响系统不能覆盖用户；改动一律临时；高影响受限；循环限流。
绝大部分断言不需要 LLM（限流检查在调用之前）。工件 tests/artifacts/t17_permissions.json
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PORT = 4827
BASE = "http://127.0.0.1:%d" % PORT
ART = Path(__file__).parent / "artifacts"
ART.mkdir(exist_ok=True)


def kill_port(port):
    out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, errors='replace', timeout=20).stdout
    pids = {p.split()[4] for p in out.splitlines()
            if len(p.split()) >= 5 and p.split()[1].endswith(":" + str(port)) and p.split()[3] == "LISTENING"}
    for pid in pids:
        subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
    time.sleep(1)


def start_engine(db, persona_path):
    env = dict(os.environ)
    env["PAE_DB_PATH"] = str(db)
    env["PAE_PERSONA_PATH"] = str(persona_path)
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
    req = urllib.request.Request(BASE + "/v1/cap/" + name, data=json.dumps(body or {}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


def err(r):
    return str((r[1] or {}).get("error") or "")


res = {}
tmpd = Path(tempfile.mkdtemp())
db = tmpd / "t17.db"
import shutil
shutil.copy(ROOT / "persona" / "default.json", tmpd / "persona.json")
kill_port(PORT)
eng = start_engine(db, tmpd / "persona.json")
try:
    assert wait_health(), "engine down"

    # ---- A) 不太大：自我扩权被拒 / 收紧自己放行 ----
    r = cap("pae.propose", {"actor": "companion", "patch": [{"key": "daily_say_cap", "to": 25}],
                            "rationale": "想多说话", "ttl_days": 7})
    res["raise_own_cap"] = (r[0], err(r)[:150])
    r = cap("pae.propose", {"actor": "companion", "patch": [{"key": "daily_say_cap", "to": 4}],
                            "rationale": "我说太多了，收一收", "ttl_days": 7})
    res["lower_own_cap"] = (r[0], (r[1].get("result") or {}).get("applied"))

    # ---- B) 不太大：用户事实受保护 ----
    r = cap("pae.remember", {"actor": "user", "kind": "preference", "text": "用户自己写的事实：不喜欢被问问题",
                             "evidence": ["user_said_so"]})
    user_fid = (r[1].get("result") or {}).get("fact_id")
    r = cap("pae.remember", {"actor": "companion", "kind": "vocab_context", "text": "AI 写的事实：他在啃 contention",
                             "evidence": ["events"]})
    ai_fid = (r[1].get("result") or {}).get("fact_id")
    r = cap("pae.forget", {"actor": "companion", "fact_id": user_fid})
    res["ai_forget_user_fact"] = (r[0], err(r)[:120])
    r = cap("pae.forget", {"actor": "companion", "fact_id": ai_fid})
    res["ai_forget_own_fact"] = (r[0], (r[1].get("result") or {}).get("changed"))

    # ---- C) 不太大：淘汰优先淘汰 AI 自己写的 ----
    cap("pae.remember", {"actor": "companion", "kind": "event", "text": "AI事实A", "evidence": ["e"]})
    cap("pae.remember", {"actor": "companion", "kind": "event", "text": "AI事实B", "evidence": ["e"]})
    cap("pae.remember", {"actor": "companion", "kind": "event", "text": "AI事实C", "evidence": ["e"]})
    cap("pae.remember", {"actor": "companion", "kind": "event", "text": "AI事实D", "evidence": ["e"]})
    cap("pae.remember", {"actor": "companion", "kind": "event", "text": "AI事实E", "evidence": ["e"]})
    r = cap("pae.memory.search", {"query": "用户自己写的", "k": 5})
    res["user_fact_survived"] = len(r[1].get("result") or [])

    # ---- D) 人格：身份锁定 / 步长 / 漂移上限 ----
    from pae_core import params as pm
    from pae_core import persona as persona_mod
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    guard = lambda p: pm.persona_guard(conn, "companion", p)
    # M19 后 persona_guard 要求非 user 提交**完整回传全部身份字段**（防省略静默清空），
    # 因此下面三条用例必须带上完整身份，否则会先被身份校验拦下、永远走不到 step/drift 分支
    # （这会让你误以为 drift 守门坏了——实测它一直好着，只是理由被前置校验遮蔽）。
    _base = persona_mod.load_params()

    def with_identity(patch):
        d = dict(_base)
        t = dict(_base.get("traits") or {})
        if "traits" in patch:
            t.update(patch["traits"])
            patch = {k: v for k, v in patch.items() if k != "traits"}
        d.update(patch)
        d["traits"] = t
        return d

    ok1, iss1, _ = guard(with_identity({"traits": {"warmth": 0.95}}))
    res["drift_exact_ok_but_step_fail"] = (ok1, iss1[:1])
    ok2, iss2, _ = guard(with_identity({"traits": {"warmth": 0.96}}))
    res["drift_exceeded"] = (ok2, iss2[:1])
    ok3, iss3, _ = guard(with_identity({"name": "新名字"}))
    res["identity_locked"] = (ok3, iss3[:1])
    conn.close()

    # ---- E) 限流：tick 不烧额度 / pin 配额 ----
    c = sqlite3.connect(str(db))
    c.execute("INSERT INTO agent_log(ts, actor, stimulus, thought, actions, result, elapsed_ms)"
              " VALUES(?,?,?,?,?,?,?)", (int(time.time()), "companion", "fake", "刚跑过", "[]", "{}", 100))
    c.commit(); c.close()
    r = cap("pae.agent.tick", {"actor": "companion", "stimulus": "马上再来一次"})
    res["tick_rate_limited"] = (r[0], (r[1].get("result") or {}).get("error"))
    c = sqlite3.connect(str(db))
    day0 = int(time.time()) // 86400 * 86400
    for i in range(20):
        c.execute("INSERT INTO events(event_id, idem_key, ts, type, payload, source) VALUES(?,?,?,?,?,?)",
                  ("evt_pin_%d" % i, "pin-%d" % i, day0 + 10, "user_pin", json.dumps({"lemma": "w%d" % i}), "companion"))
    c.commit(); c.close()
    r = cap("pae.pin", {"actor": "companion", "lemma": "quartz"})
    res["pin_quota"] = (r[0], err(r)[:110])
    r = cap("pae.pin", {"actor": "user", "lemma": "quartz"})
    res["user_pin_unlimited"] = (r[0], (r[1].get("result") or {}).get("ok"))

    # ---- F) 足够大：AI 能做的事仍然做得到 ----
    r = cap("pae.propose", {"actor": "companion", "patch": [{"key": "annotate_max_s", "to": 0.62}],
                            "rationale": "注解太密", "ttl_days": 7})
    res["ai_can_tune_normal"] = (r[0], (r[1].get("result") or {}).get("applied"))
    r = cap("pae.remember", {"actor": "companion", "kind": "goal", "text": "他想把英语变成阅读副产品",
                             "evidence": ["events"]})
    res["ai_can_remember"] = (r[0], bool((r[1].get("result") or {}).get("fact_id")))
    r = cap("pae.say", {"actor": "companion", "text": "一句话"})
    res["ai_can_say"] = (r[0], (r[1].get("result") or {}).get("delivered"))
    r = cap("pae.permissions", {})
    res["permissions_exposed"] = (r[0], sorted((r[1].get("result") or {}).keys())[:5])
finally:
    eng.terminate(); kill_port(PORT)

ok = (res.get("raise_own_cap", [None, ""])[0] == 500 and "self-binding" in res.get("raise_own_cap", ["", ""])[1]
      and res.get("lower_own_cap", [None, None])[1] is True
      and res.get("ai_forget_user_fact", [None, ""])[0] == 500
      and res.get("ai_forget_own_fact", [None, None])[1] is True
      and res.get("user_fact_survived") == 1
      and res.get("drift_exceeded", [None, []])[0] is False and "drift" in str(res.get("drift_exceeded", [None, []])[1])
      and res.get("identity_locked", [None, []])[0] is False
      and res.get("tick_rate_limited", [None, None])[1] == "rate_limited"
      and res.get("pin_quota", [None, ""])[0] == 500
      and res.get("user_pin_unlimited", [None, None])[1] is True
      and res.get("ai_can_tune_normal", [None, None])[1] is True
      and res.get("ai_can_remember", [None, None])[1] is True
      and res.get("ai_can_say", [None, None])[1] is True
      and res.get("permissions_exposed", [None, []])[0] == 200)
res["T17"] = "PASS" if ok else "FAIL"
(ART / "t17_permissions.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(res, ensure_ascii=False, indent=1))
print("T17 PERMISSIONS:", res["T17"])