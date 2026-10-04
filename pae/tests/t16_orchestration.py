# -*- coding: utf-8 -*-
"""T16: 智能体编排的边界 —— 有界自主 / 高影响变量 / 人格自改 / 自我感知。

验证「AI 能做很多，但翻不了天」：
- 非 user 的改动必须带 ttl（临时）
- 高影响变量：步长 ≤ 区间 20%，全局每天 ≤1 次
- AI 只能微调情绪轴（±0.05、同轴 7 天冷却），不能改写身份
- 感知面含「自己」（最近思考/今天说过几句/已改变量）
工件 tests/artifacts/t16_orchestration.json
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
PORT = 4826
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


def start_engine(db, persona_path=None):
    env = dict(os.environ); env["PAE_DB_PATH"] = str(db)
    if persona_path:
        env["PAE_PERSONA_PATH"] = str(persona_path)   # 测试不许改用户真实人格
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


def cap(name, body=None, key=None):
    hdr = {"Content-Type": "application/json"}
    if key:
        hdr["X-PAE-Key"] = key
    req = urllib.request.Request(BASE + "/v1/cap/" + name, data=json.dumps(body or {}).encode(), headers=hdr)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


def err_of(resp):
    return str((resp[1] or {}).get("error") or "")


res = {}
tmpd = Path(tempfile.mkdtemp())
db = tmpd / "t16.db"
persona_tmp = tmpd / "persona.json"
import shutil
shutil.copy(ROOT / "persona" / "default.json", persona_tmp)
kill_port(PORT)
eng = start_engine(db, persona_tmp)
try:
    assert wait_health(), "engine down"

    # ---- A) AI 的改动必须带 ttl（本地用户不受限）----
    r = cap("pae.propose", {"actor": "companion", "patch": [{"key": "annotate_max_s", "to": 0.6}],
                            "rationale": "无 ttl 试试"})
    res["ai_no_ttl"] = (r[0], err_of(r)[:260])
    r = cap("pae.propose", {"actor": "companion", "patch": [{"key": "annotate_max_s", "to": 0.6}],
                            "rationale": "带 ttl", "ttl_days": 7})
    res["ai_with_ttl"] = (r[0], (r[1].get("result") or {}).get("applied"))
    r = cap("pae.propose", {"patch": [{"key": "annotate_max_s", "to": 0.62}],
                            "rationale": "本机用户（无 key=local）永久改，应放行"})
    res["local_user_permanent"] = (r[0], (r[1].get("result") or {}).get("applied"),
                                   (r[1].get("result") or {}).get("actor"))

    # ---- B) 高影响变量：步长与每日额度 ----
    r = cap("pae.propose", {"actor": "companion",
                            "patch": [{"key": "half_life_days", "to": 14}],
                            "rationale": "大幅缩短半衰期", "ttl_days": 7})
    res["hi_big_step"] = (r[0], err_of(r)[:260])
    r = cap("pae.propose", {"actor": "companion",
                            "patch": [{"key": "half_life_days", "to": 80}],
                            "rationale": "小幅调整半衰期", "ttl_days": 14})
    res["hi_small_step"] = (r[0], (r[1].get("result") or {}).get("applied"),
                            (r[1].get("result") or {}).get("gate_result", "")[:80])
    r = cap("pae.propose", {"actor": "analyst",
                            "patch": [{"key": "band_hi", "to": 0.60}],
                            "rationale": "同一天再动高影响变量", "ttl_days": 7})
    res["hi_daily_limit"] = (r[0], err_of(r)[:260])

    # ---- C) 人格自改守门 ----
    base = (cap("pae.persona", {})[1].get("result") or {})
    cur = json.loads(json.dumps(base.get("params") or {}))
    def with_trait(p, axis, val):
        q = json.loads(json.dumps(p))
        q.setdefault("traits", {})[axis] = val
        return q
    r = cap("pae.persona.update", {"actor": "companion",
                                   "params": dict(cur, name="我要改个名")})
    res["ai_change_identity"] = (r[0], err_of(r)[:80])
    r = cap("pae.persona.update", {"actor": "companion",
                                   "params": with_trait(cur, "warmth", 0.95)})
    res["ai_big_trait_step"] = (r[0], err_of(r)[:80])
    r = cap("pae.persona.update", {"actor": "companion",
                                   "params": with_trait(cur, "warmth", (cur.get("traits") or {}).get("warmth", 0.7) + 0.03)})
    res["ai_small_trait_step"] = (r[0], (r[1].get("result") or {}).get("axes_changed"))
    cur2 = (cap("pae.persona", {})[1].get("result") or {}).get("params") or cur
    r = cap("pae.persona.update", {"actor": "companion",
                                   "params": with_trait(cur2, "warmth", (cur2.get("traits") or {}).get("warmth", 0.73) + 0.02)})
    res["ai_axis_cooldown"] = (r[0], err_of(r)[:90])
    r = cap("pae.persona.update", {"params": with_trait(cur2, "warmth", 0.95)})
    res["local_user_persona"] = (r[0], (r[1].get("result") or {}).get("axes_changed"))

    # ---- D) 自我感知 ----
    ctx = (cap("pae.context", {})[1].get("result") or {})
    res["context_has_self"] = sorted((ctx.get("self") or {}).keys())
    cap("pae.say", {"actor": "companion", "text": "试说一句"})
    ctx2 = (cap("pae.context", {})[1].get("result") or {})
    res["said_today_reflected"] = (ctx2.get("self") or {}).get("said_today")
    res["overrides_visible"] = list(((ctx2.get("self") or {}).get("current_overrides") or {}).keys())
finally:
    eng.terminate(); kill_port(PORT)

ok = (res.get("ai_no_ttl", [None, ""])[0] == 500 and "ttl" in res.get("ai_no_ttl", ["", ""])[1]
      and res.get("ai_with_ttl", [None, None])[1] is True
      and res.get("local_user_permanent", [None, None])[1] is True
      and res.get("hi_big_step", [None, ""])[0] == 500 and "步长" in res.get("hi_big_step", ["", ""])[1]
      and res.get("hi_small_step", [None, None])[1] is True
      and res.get("hi_daily_limit", [None, ""])[0] == 500
      and res.get("ai_change_identity", [None, ""])[0] == 500
      and res.get("ai_big_trait_step", [None, ""])[0] == 500
      and res.get("ai_small_trait_step", [None, None])[1]
      and res.get("ai_axis_cooldown", [None, ""])[0] == 500
      and res.get("local_user_persona", [None, None])[1]
      and res.get("context_has_self") == ["current_overrides", "recent_thoughts", "said_today"]
      and res.get("said_today_reflected") == 1)
res["T16"] = "PASS" if ok else "FAIL"
(ART / "t16_orchestration.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(res, ensure_ascii=False, indent=1))
print("T16 ORCHESTRATION:", res["T16"])