# -*- coding: utf-8 -*-
"""T22: 结果侧尺子 —— conversion_rate 与决策效果回填。工件 tests/artifacts/t22_outcomes.json"""
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
PORT = 4832
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
db = tmpd / "t22.db"
kill_port(PORT)
env = dict(os.environ)
env["PAE_DB_PATH"] = str(db)
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

    cap("pae.status")   # 先触发引擎初始化（建表）——引擎是惰性的
    time.sleep(1)

    # 造一个「成熟队列」：10 天前被注解的三个词，分别 转化 / 只看不点 / 完全无视；再放一个太新的词
    # M15 早期队列（[now-14d, now-3d] 内、且未进成熟队列，即 3-7 天前首遇）：只放 earlybird 一个词——
    # 注意 conv/eng/silent 首遇在 10 天前、早于成熟窗口上界（now-7d），它们**不该**进早期队列。
    now = int(time.time())
    old = now - 10 * 86400
    early = now - 4 * 86400          # 4 天前首遇：落在早期窗口
    c = sqlite3.connect(str(db))
    rows = [
        ("a1", "conv", old, "annotation_shown"),
        ("a2", "conv", old + 3600, "hover"),
        ("a3", "conv", old + 7200, "known_click"),
        ("b1", "eng", old, "annotation_shown"),
        ("b2", "eng", old + 3600, "hover"),
        ("c1", "silent", old, "annotation_shown"),
        ("d1", "fresh", now - 2 * 86400, "annotation_shown"),
        ("e1", "earlybird", early, "annotation_shown"),
        ("e2", "earlybird", now - 3 * 86400 + 600, "known_click"),
    ]
    for eid, lemma, ts, typ in rows:
        c.execute("INSERT INTO events(event_id, idem_key, ts, type, payload, source) VALUES(?,?,?,?,?,?)",
                  (eid, eid, ts, typ, json.dumps({"lemma": lemma}), "test"))
    # 决策效果回填用的事件：一条决策前/后台的曝光与悬停。
    # mid 放 2 天前：这批 p/q 词必须**同时避开**成熟队列与早期队列（它们只是决策回填素材，
    # 不该污染任何队列计数）——2 天前比早期窗口上界（now-3d）更晚，两个队列都收不到。
    mid = now - 2 * 86400
    for i in range(8):
        c.execute("INSERT INTO events(event_id, idem_key, ts, type, payload, source) VALUES(?,?,?,?,?,?)",
                  ("pre%d" % i, "pre%d" % i, mid - 3600 * (i + 1), "annotation_shown", json.dumps({"lemma": "p%d" % i}), "t"))
        c.execute("INSERT INTO events(event_id, idem_key, ts, type, payload, source) VALUES(?,?,?,?,?,?)",
                  ("post%d" % i, "post%d" % i, mid + 3600 * (i + 1), "annotation_shown", json.dumps({"lemma": "q%d" % i}), "t"))
    for i in range(6):
        c.execute("INSERT INTO events(event_id, idem_key, ts, type, payload, source) VALUES(?,?,?,?,?,?)",
                  ("ph%d" % i, "ph%d" % i, mid + 7200 + i, "hover", json.dumps({"lemma": "q%d" % i}), "t"))
    c.execute("INSERT INTO decisions(decision_id, ts, actor, capability, patch, rationale, before, after,"
              " applied, gate_result) VALUES(?,?,?,?,?,?,?,?,1,?)",
              ("dec_t22", mid, "companion", "pae.propose", json.dumps([{"key": "annotate_max_s", "to": 0.6}]),
               "测试", "{}", json.dumps({"annotate_max_s": 0.6}), "ok"))
    c.commit()
    c.close()

    st, r = cap("pae.outcomes", {"eval_days": 7})
    o = r.get("result") or {}
    res["status"] = st
    res["cohort_size"] = o.get("cohort_size")
    res["converted"] = o.get("converted")
    res["ignored"] = o.get("ignored")
    res["conversion_rate"] = o.get("conversion_rate")
    res["engagement_rate"] = o.get("engagement_rate")
    res["median_days_to_known"] = o.get("median_days_to_known")
    # M15：早期读数（3 天窗口，见 metrics.compute_outcomes）
    res["early_cohort_size"] = o.get("early_cohort_size")
    res["early_conversion_rate"] = o.get("early_conversion_rate")
    res["early_note"] = o.get("early_note")
    eff = o.get("decision_effects") or []
    res["decision_effects_count"] = len(eff)
    res["decision_verdict"] = (eff[0].get("verdict") if eff else None)
    res["decision_hover_rates"] = ([eff[0].get("hover_rate_before"), eff[0].get("hover_rate_after")]
                                   if eff else None)
    # 回填必须落库
    c = sqlite3.connect(str(db))
    got = c.execute("SELECT effect_result FROM decisions WHERE decision_id = 'dec_t22'").fetchone()
    res["effect_persisted"] = bool(got and got[0])
    c.close()
finally:
    eng.terminate()
    kill_port(PORT)

ok = (res.get("status") == 200 and res.get("cohort_size") == 3 and res.get("converted") == 1
      and res.get("ignored") == 1 and res.get("conversion_rate") == round(1 / 3, 3)
      # M15：成熟种子（10 天前）不进早期队列；只有 4 天前的 earlybird 进，且它转化了
      and res.get("early_cohort_size") == 1 and res.get("early_conversion_rate") == 1.0
      and bool(res.get("early_note"))
      and res.get("decision_effects_count", 0) >= 1 and res.get("effect_persisted"))
res["T22"] = "PASS" if ok else "FAIL"
(ART / "t22_outcomes.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(res, ensure_ascii=False, indent=1))
print("T22 OUTCOMES:", res["T22"])