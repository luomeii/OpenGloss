# -*- coding: utf-8 -*-
"""T21: 接线修复 —— 记忆能被想起 / limits 读有效参数 / 死旋钮有消费者。

对应 38 号 P1：两个独立评审都指出的「记住但不回读」断线。工件 tests/artifacts/t21_wiring.json
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
PORT = 4831
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


def cap(name, body=None):
    req = urllib.request.Request(BASE + "/v1/cap/" + name, data=json.dumps(body or {}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


res = {}
tmpd = Path(tempfile.mkdtemp())
import shutil
shutil.copy(ROOT / "persona" / "default.json", tmpd / "persona.json")
kill_port(PORT)
env = dict(os.environ)
env["PAE_DB_PATH"] = str(tmpd / "t21.db")
env["PAE_PERSONA_PATH"] = str(tmpd / "persona.json")
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

    # 先放一条事实 + 一轮对话（模拟「曾经记住过」）
    cap("pae.remember", {"kind": "preference", "text": "他不喜欢被问「学会了吗」", "evidence": ["user_said"]})
    st, s = cap("pae.session.open", {"actor": "companion"})
    sid = (s.get("result") or {}).get("session_id")
    cap("pae.session.turn", {"session_id": sid, "role": "user", "text": "contention 和 conflict 啥区别"})
    cap("pae.session.turn", {"session_id": sid, "role": "agent", "text": "一个是抢资源，一个是两边对不上"})

    # ① 记忆必须出现在 context 里（修「记住但不回读」）
    c = (cap("pae.context", {"top_n": 5})[1].get("result") or {})
    mem = c.get("memory") or {}
    res["context_has_memory_block"] = "memory" in c
    res["memory_facts_count"] = len(mem.get("facts") or [])
    res["memory_turns_count"] = len(mem.get("recent_turns") or [])
    res["memory_fact_text"] = ((mem.get("facts") or [{}])[0] or {}).get("text")

    # ② limits 读有效参数：改门槛后，同一份 context 的 limits 必须跟着变（修自我感知漂移）
    before = ((c.get("limits") or {}).get("annotate_max_s"))
    st, pr = cap("pae.propose", {"patch": [{"key": "annotate_max_s", "to": 0.62}],
                                 "rationale": "测试：验证 limits 跟随有效参数"})
    c2 = (cap("pae.context", {"top_n": 5})[1].get("result") or {})
    after = ((c2.get("limits") or {}).get("annotate_max_s"))
    res["limits_before_after"] = [before, after]
    res["limits_source"] = (c2.get("limits") or {}).get("source")
    res["limits_follows_effective"] = (after == 0.62 and after != before)

    # ③ 死旋钮有消费者：max_ann_per_page 出现在有效参数里，且扩展能读到（SW 走 pae.params）
    p = (cap("pae.params", {})[1].get("result") or {})
    eff = p.get("effective") or {}
    res["params_has_page_cap"] = ("max_ann_per_page" in eff, eff.get("max_ann_per_page"))
    res["page_cap_is_proposable"] = "max_ann_per_page" in ((p.get("ranges") or {}) and
                                                           set((p.get("ranges") or {}).keys()))
    # 让 AI 提案改它（带 ttl），验证守门放行且值真的变
    st2, pr2 = cap("pae.propose", {"actor": "companion",
                                   "patch": [{"key": "max_ann_per_page", "to": 24}],
                                   "rationale": "页面注解太多", "ttl_days": 3})
    eff2 = ((cap("pae.params", {})[1].get("result") or {}).get("effective") or {})
    res["page_cap_changed"] = (st2, eff2.get("max_ann_per_page"))
finally:
    eng.terminate()
    kill_port(PORT)

ok = (res.get("context_has_memory_block") and res.get("memory_facts_count", 0) >= 1
      and res.get("memory_turns_count", 0) >= 2
      and res.get("limits_follows_effective") and res.get("limits_source") == "effective_params"
      and res.get("params_has_page_cap", [False])[0]
      and res.get("page_cap_changed", [None, None])[1] == 24)
res["T21"] = "PASS" if ok else "FAIL"
(ART / "t21_wiring.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(res, ensure_ascii=False, indent=1))
print("T21 WIRING:", res["T21"])