# -*- coding: utf-8 -*-
"""T19: 低频调度 —— AI 能不能「有节奏地活着」，且不烧额度、不吵人。

全部断言不花 LLM 额度（触发判定是纯 SQL；开启/关闭不重启引擎）。工件 tests/artifacts/t19_scheduler.json
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
PORT = 4829
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


def cap(name, body=None):
    req = urllib.request.Request(BASE + "/v1/cap/" + name, data=json.dumps(body or {}).encode(),
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
tmpd = Path(tempfile.mkdtemp())
db = tmpd / "t19.db"
kill_port(PORT)
sched_cfg = tmpd / "sched_config.json"
sched_cfg.write_text(json.dumps({"agent_schedule": {"enabled": False, "interval_min": 30,
                                                    "min_new_events": 3}}, ensure_ascii=False), encoding="utf-8")
env = dict(os.environ)
env["PAE_DB_PATH"] = str(db)
env["PAE_SCHEDULE_CONFIG"] = str(sched_cfg)   # 测试不许改真实 config
os.environ["PAE_SCHEDULE_CONFIG"] = str(sched_cfg)   # 测试进程自己也要读同一个（模块导入时解析）
eng = subprocess.Popen([sys.executable, "-m", "uvicorn", "pae_core.api:app", "--port", str(PORT)],
                       cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    end = time.time() + 60
    while time.time() < end:
        try:
            urllib.request.urlopen(BASE + "/v1/health", timeout=3)
            break
        except Exception:
            time.sleep(0.5)

    from pae_core import scheduler as sch
    # A) 默认关闭 → 不跑（也不花额度）
    st = (cap("pae.agent.schedule", {})[1].get("result") or {})
    res["default_enabled"] = st.get("enabled")
    res["default_reason"] = st.get("reason")
    res["default_thread_alive"] = st.get("thread_alive")

    conn = sqlite3.connect(str(db))
    res["skipped_when_disabled"] = sch.maybe_tick(conn).get("skipped")
    conn.close()

    # B) 用户开启（interval 0 便于判定）→ would_tick_now 需有新活动
    r = cap("pae.agent.schedule.set", {"enabled": True, "interval_min": 1, "min_new_events": 3})
    res["user_can_enable"] = (r[0], (r[1].get("result") or {}).get("config", {}).get("enabled"))
    st2 = (cap("pae.agent.schedule", {})[1].get("result") or {})
    res["no_activity_reason"] = st2.get("reason")

    # 造点事件 + 把上次 tick 时间推到过去 → 应判定为该跑（但不真跑，避免花额度）
    conn = sqlite3.connect(str(db))
    for i in range(5):
        conn.execute("INSERT INTO events(event_id, idem_key, ts, type, payload, source) VALUES(?,?,?,?,?,?)",
                     ("evt_s%d" % i, "s%d" % i, int(time.time()), "encounter",
                      json.dumps({"lemma": "w%d" % i}), "api"))
    conn.execute("INSERT INTO agent_log(ts, actor, stimulus, thought, actions, result, elapsed_ms) VALUES(?,?,?,?,?,?,?)",
                 (int(time.time()) - 4000, "companion", "旧", "旧", "[]", "{}", 10))
    conn.commit()
    ok_now, why_now = sch.should_tick(conn)
    res["should_tick_with_activity"] = [ok_now, why_now]
    # 限流：造一条刚跑过的记录 → too_soon
    conn.execute("INSERT INTO agent_log(ts, actor, stimulus, thought, actions, result, elapsed_ms) VALUES(?,?,?,?,?,?,?)",
                 (int(time.time()), "companion", "刚", "刚", "[]", "{}", 10))
    conn.commit()
    ok2, why2 = sch.should_tick(conn)
    res["should_tick_too_soon"] = [ok2, why2]
    conn.close()

    # C) AI 不能给自己开调度（自我扩权防护的延伸）
    cap("pae.agent.schedule.set", {"enabled": False})
    r = cap("pae.agent.schedule.set", {"actor": "companion", "enabled": True})
    res["ai_cannot_enable"] = (r[0], str(r[1].get("error") or "")[:60])
finally:
    eng.terminate()
    kill_port(PORT)

ok = (res.get("default_enabled") is False and res.get("default_reason") == "disabled"
      and res.get("skipped_when_disabled") == "disabled"
      and res.get("user_can_enable", [None, None])[1] is True
      and res.get("no_activity_reason") in ("no_new_activity", "interval", "too_soon")
      and res.get("should_tick_with_activity", [None, None])[0] is True
      and res.get("should_tick_too_soon", [None, None])[1] == "too_soon"
      and res.get("ai_cannot_enable", [None, ""])[0] == 500)
res["T19"] = "PASS" if ok else "FAIL"
(ART / "t19_scheduler.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(res, ensure_ascii=False, indent=1))
print("T19 SCHEDULER:", res["T19"])