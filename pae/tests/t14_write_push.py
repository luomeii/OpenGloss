# -*- coding: utf-8 -*-
"""T14: 写能力 + 推送 —— 提案真改行为 / 四道守门 / ttl 回滚 / SSE 送达 / 每日上限。
工件 tests/artifacts/t14_write_push.json
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PORT = 4824  # 独立端口：不与浏览器测试抢 4815，避免端口竞争
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
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


res = {}
db = Path(tempfile.mkdtemp()) / "t14.db"
kill_port(PORT)
eng = start_engine(db)
try:
    assert wait_health(), "engine down"

    # A) 造一个 S 已升高的词：8 次遭遇（不同页面，各自计一次）
    for i in range(5):
        cap("pae.annotate", {"text": "quartz", "page_id": "t14-seed-%d" % i})
    st_w, w = cap("pae.word", {"lemma": "quartz"})
    res["quartz_s"] = (w.get("result") or {}).get("s_value")
    # 提案前：它仍在门槛内会被注解
    before = cap("pae.annotate", {"text": "quartz", "page_id": "t14-a"})[1].get("result")
    res["annotated_before"] = len(before or [])
    res["threshold_before"] = 0.75

    # B) 守门：非法提案
    res["gate_missing_rationale"] = cap("pae.propose", {"patch": [{"key": "annotate_max_s", "to": 0.2}]})[1].get("error")
    res["gate_bad_key"] = cap("pae.propose", {"patch": [{"key": "foo", "to": 1}], "rationale": "x"})[1].get("error")
    res["gate_out_of_range"] = cap("pae.propose",
        {"patch": [{"key": "annotate_max_s", "to": 9.9}], "rationale": "越界测试"})[1].get("error")
    res["gate_range_detail"] = (cap("pae.propose",
        {"patch": [{"key": "w_hover", "to": 5.0}], "rationale": "越界"})[1].get("error") or {})

    # C) 合法提案：门槛降到 0.55 → S 已到 0.57 的词不再被注解，但生词照旧
    st, ok = cap("pae.propose", {"patch": [{"key": "annotate_max_s", "to": 0.60}],
                                 "rationale": "注解太密，先降门槛", "evidence": ["pae.metrics"],
                                 "ttl_days": 7})
    res["propose_ok"] = [st, (ok.get("result") or {}).get("applied"),
                         (ok.get("result") or {}).get("gate_result")]
    after = cap("pae.annotate", {"text": "quartz", "page_id": "t14-c"})[1].get("result")
    res["annotated_after"] = len(after or [])
    fresh = cap("pae.annotate", {"text": "ubiquitous", "page_id": "t14-c2"})[1].get("result")
    res["fresh_still_annotated"] = len(fresh or [])
    res["behavior_changed"] = (res["annotated_before"] > 0 and res["annotated_after"] == 0
                               and res["fresh_still_annotated"] > 0)

    # D) 冷却：同 actor 同键再提（合法值）→ 应被冷却拦
    res["gate_cooldown"] = cap("pae.propose", {"patch": [{"key": "annotate_max_s", "to": 0.65}],
                                               "rationale": "再来一次"})[1].get("error")

    # E) ttl 回滚：先确认覆盖在 → 把到期时间改到过去 → 自动回滚
    import sqlite3
    ov_before = (cap("pae.params", {})[1].get("result") or {}).get("overrides") or {}
    res["override_present_before_rollback"] = "annotate_max_s" in ov_before
    c = sqlite3.connect(str(db))
    c.execute("UPDATE params SET expires_at = ? WHERE key = 'annotate_max_s'", (int(time.time()) - 10,))
    c.commit()
    c.close()
    time.sleep(6)  # 等过参数陈旧窗（引擎 5 秒后自动刷新 → 惰性回滚到期项）
    eb = (cap("pae.params", {})[1].get("result")) or {}
    res["override_after_expiry"] = "annotate_max_s" in (eb.get("overrides") or {})
    res["effective_after_rollback"] = (eb.get("effective") or {}).get("annotate_max_s")
    res["ttl_rollback_works"] = (res["override_present_before_rollback"]
                                 and not res["override_after_expiry"]
                                 and res["effective_after_rollback"] == 0.75)

    # F) SSE：订阅 → say → 收到
    received = []
    def reader():
        try:
            with urllib.request.urlopen(BASE + "/v1/ext/stream", timeout=25) as r:
                for raw in r:
                    line = raw.decode("utf-8", "replace").strip()
                    if line.startswith("data:"):
                        try:
                            received.append(json.loads(line[5:].strip()))
                        except Exception:
                            pass
                    if len(received) >= 2:
                        break
        except Exception:
            pass
    th = threading.Thread(target=reader, daemon=True)
    th.start()
    time.sleep(1.5)
    st, sayr = cap("pae.say", {"text": "contention 你盯了三回了，收了吧", "anchor": "p"})
    res["say_ok"] = (st, (sayr.get("result") or {}).get("delivered"),
                     (sayr.get("result") or {}).get("subscribers"))
    th.join(timeout=20)
    res["sse_received"] = [e.get("type") for e in received]
    res["sse_got_say"] = any(e.get("type") == "say" for e in received)

    # G) 每日上限：把 cap 设 1（用 pin/直接改 params 表，避开冷却）→ 第二条 say 被拦
    c = sqlite3.connect(str(db))
    c.execute("INSERT INTO params(key, value, updated_at, actor, decision_id) VALUES('daily_say_cap','1',?,?,'test')"
              " ON CONFLICT(key) DO UPDATE SET value='1'", (int(time.time()), "test"))
    c.commit()
    c.close()
    s1 = cap("pae.say", {"text": "第二条"})
    s2 = cap("pae.say", {"text": "第三条"})
    res["cap_first"] = s1[0]
    res["cap_second_blocked"] = (s2[0], (s2[1].get("error") or "")[:40])
    res["cap_blocks_after_limit"] = (s1[0] == 500 and s2[0] == 500)
    st, lg = cap("pae.push.log", {"n": 10})
    res["push_log_blocked_present"] = any(x.get("blocked") for x in (lg.get("result") or []))

    # H) hint / pin / trend / decisions
    res["hint"] = cap("pae.hint", {"lemma": "quartz", "level": "up"})[1].get("result", {}).get("delivered")
    res["pin"] = (cap("pae.pin", {"lemma": "intensity"})[1].get("result") or {}).get("ok")
    res["trend_days"] = len((cap("pae.trend", {"days": 7})[1].get("result") or {}).get("series") or [])
    res["decisions"] = len(cap("pae.decisions", {"n": 10})[1].get("result") or [])
    res["grammar_stub"] = cap("pae.grammar", {})[1].get("result", {}).get("state")
finally:
    eng.terminate(); kill_port(PORT)

ok = (res.get("annotated_before", 0) > 0 and res.get("behavior_changed") and res.get("ttl_rollback_works")
      and res.get("sse_got_say") and res.get("cap_blocks_after_limit")
      and res.get("push_log_blocked_present") and res.get("hint") and res.get("pin")
      and res.get("decisions", 0) >= 1 and res.get("grammar_stub") == "not_tracked"
      and ("冷却" in str(res.get("gate_cooldown")) or "conflict" in str(res.get("gate_cooldown"))))
res["T14"] = "PASS" if ok else "FAIL"
(ART / "t14_write_push.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(res, ensure_ascii=False, indent=1))
print("T14 WRITE+PUSH:", res["T14"])