# -*- coding: utf-8 -*-
"""T23: 熟悉度投影（35号）—— R1-R8 判定 / shadow 零行为变化 / 一键恢复 / 可重建一致。

工件 tests/artifacts/t23_familiarity.json"""
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
PORT = 4833
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
db = tmpd / "t23.db"
cfg = tmpd / "config_dummy.json"
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
    cap("pae.status")   # 触发建表
    time.sleep(1)

    now = int(time.time())
    day = 86400
    c = sqlite3.connect(str(db))

    def exp(eid, lemma, ts, active=3000, page=None):
        payload = {"lemma": lemma, "page_id": page or ("p-" + eid), "page_active_ms": active}
        c.execute("INSERT INTO events(event_id, idem_key, ts, type, payload, source) VALUES(?,?,?,?,?,?)",
                  (eid, eid, ts, "annotation_shown", json.dumps(payload), "test"))

    # X：合格豁免（8 次曝光跨 3 天、零介入、首次 10 天前）
    for i in range(8):
        exp("x%d" % i, "quartz", now - (10 - i) * day)
    # Y：有 1 次悬停 → R3 拦
    for i in range(8):
        exp("y%d" % i, "tenacious", now - (10 - i) * day)
    c.execute("INSERT INTO events(event_id, idem_key, ts, type, payload, source) VALUES(?,?,?,?,?,?)",
              ("yh", "yh", now - 5 * day, "hover", json.dumps({"lemma": "tenacious"}), "test"))
    # Z：8 次曝光但全在一天 → R2 拦
    for i in range(8):
        exp("z%d" % i, "ubiquitous", now - 9 * day - i, page="p-z%d" % i)
    # W：只有 4 次 → R1 拦（R1=5；原 7 次在旧阈值下测，现阈值降了）
    for i in range(4):
        exp("w%d" % i, "meticulous", now - (10 - i) * day)
    # T：6 次曝光跨 3 天、首遇 9 天前、零介入 → **应被豁免**（验证死锁解除：旧 R1=8 下永远到不了）
    for i in range(6):
        exp("t%d" % i, "resilient", now - (9 - i) * day)
    # V：够次数但首次仅 3 天前 → R5 新词保护
    for i in range(8):
        exp("v%d" % i, "redundant", now - (3 - i * 0.2) * day if False else now - 3 * day + i * 3600)
    # U：曝光全部不合格（page_active_ms=200）→ 不计入
    for i in range(8):
        exp("u%d" % i, "perfunctory", now - (10 - i) * day, active=200)
    c.commit()
    c.close()

    st, r = cap("pae.familiarity", {"recompute": True, "limit": 20})
    o = r.get("result") or {}
    res["status"] = st
    res["mode"] = o.get("mode")
    res["enforce_active"] = o.get("enforce_active")
    res["would_exempt"] = sorted([x.get("lemma") for x in (o.get("would_exempt") or [])])
    res["near_miss_lemmas"] = sorted([x.get("lemma") for x in (o.get("near_miss") or [])])
    res["rule_version"] = o.get("rule_version")

    # shadow 零行为变化：quartz 仍应被正常注解
    st2, ann = cap("pae.annotate", {"text": "quartz tenacious", "page_id": "t23-shadow"})
    res["shadow_still_annotates"] = sorted([a.get("lemma") for a in (ann.get("result") or [])])

    # 一键恢复 → 冷却期不再豁免
    st3, rs = cap("pae.familiarity.restore", {"lemma": "quartz"})
    st4, r2 = cap("pae.familiarity", {"recompute": True})
    res["after_restore_exempt"] = sorted([x.get("lemma") for x in ((r2.get("result") or {}).get("would_exempt") or [])])
    res["restore_result"] = rs.get("result")

    # 可重建一致性：连算两次结果相同
    a1 = sorted([x.get("lemma") for x in ((cap("pae.familiarity", {"recompute": True})[1].get("result") or {}).get("would_exempt") or [])])
    a2 = sorted([x.get("lemma") for x in ((cap("pae.familiarity", {"recompute": True})[1].get("result") or {}).get("would_exempt") or [])])
    res["rebuild_identical"] = (a1 == a2)
finally:
    eng.terminate()
    kill_port(PORT)

ok = (res.get("status") == 200 and res.get("mode") == "shadow" and res.get("enforce_active") is False
      and res.get("would_exempt") == ["quartz", "resilient"]
      and "tenacious" in res.get("near_miss_lemmas", []) and "ubiquitous" in res.get("near_miss_lemmas", [])
      and "meticulous" in res.get("near_miss_lemmas", []) and "redundant" in res.get("near_miss_lemmas", [])
      and "perfunctory" in res.get("near_miss_lemmas", [])
      and "quartz" in res.get("shadow_still_annotates", [])
      and res.get("after_restore_exempt") == ["resilient"]   # 恢复只影响 quartz，resilient 应留在豁免列表
      and res.get("rebuild_identical"))
res["T23"] = "PASS" if ok else "FAIL"
(ART / "t23_familiarity.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(res, ensure_ascii=False, indent=1))
print("T23 FAMILIARITY:", res["T23"])