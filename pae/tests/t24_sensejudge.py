# -*- coding: utf-8 -*-
"""T24: 义项判定影子（P5 第一步）—— 免费规则/缓存/校验/降级/上屏不受影响。

工件 tests/artifacts/t24_sensejudge.json"""
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
PORT = 4834
BASE = "http://127.0.0.1:%d" % PORT
ART = Path(__file__).parent / "artifacts"
ART.mkdir(exist_ok=True)
DICT = str(ROOT.parent / "resources" / "ECDICT" / "dict.sqlite")


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
from pae_core import sensejudge as sj
from pae_core.db import get_db

# 找一个真·多义词与一个单义项词（都从真词典里挑）
conn_dict = sqlite3.connect(DICT)
poly, single = None, None
for w, tr in conn_dict.execute("SELECT word, translation FROM dict_words WHERE length(word) BETWEEN 5 AND 12 LIMIT 40000"):
    if not tr:
        continue
    lines = [l.strip() for l in str(tr).replace(chr(92) + "n", chr(10)).split(chr(10)) if l.strip()]
    if len(lines) >= 3 and not lines[0].startswith("[") and poly is None and str(w).isalpha():
        poly = (w.lower(), lines)
    if len(lines) == 1 and single is None and str(w).isalpha() and len(w) > 5:
        single = (w.lower(), lines)
    if poly and single:
        break
conn_dict.close()
res["poly_word"] = poly[0] if poly else None
res["poly_candidates"] = len(poly[1]) if poly else None
res["single_word"] = single[0] if single else None

tmpd = Path(tempfile.mkdtemp())
db = get_db(str(tmpd / "t24.db"))
sj.ensure_tables(db)

# A1 注入替身：合法 JSON → 应选中并写缓存
calls = {"n": 0}


def good_llm(sys_p, user_p):
    calls["n"] += 1
    lemma = res["poly_word"]
    return json.dumps({"sense_index": 1, "evidence_span": lemma})


sentence = "We need to handle the %s carefully in this pipeline." % res["poly_word"]
r1 = sj.judge(db, DICT, res["poly_word"], sentence, llm_chat=good_llm)
res["a1_chosen"] = r1.get("chosen_idx")
res["a1_fallback"] = r1.get("fallback")
res["a1_llm_calls"] = calls["n"]

# A2 同句再判 → 命中缓存，不再调 LLM
r2 = sj.judge(db, DICT, res["poly_word"], sentence, llm_chat=good_llm)
res["a2_reason"] = r2.get("reason")
res["a2_llm_calls_after"] = calls["n"]

# A3 单义项词 → 免费规则拦下，零调用
r3 = sj.judge(db, DICT, res["single_word"], "The %s is fine." % res["single_word"], llm_chat=good_llm)
res["a3_reason"] = r3.get("reason")
res["a3_llm_calls_after"] = calls["n"]

# A4 坏 JSON → 降级，不抛异常
def bad_llm(sys_p, user_p):
    return "抱歉，我不能确定"


other = "Another sentence with %s here." % res["poly_word"]
r4 = sj.judge(db, DICT, res["poly_word"], other, llm_chat=bad_llm)
res["a4_fallback"] = r4.get("fallback")
res["a4_chosen"] = r4.get("chosen_idx")

# A5 统计与缓存层级
st = sj.stats(db)
res["stats_calls_today"] = st.get("calls_today")
res["stats_cache"] = st.get("cache")

# B 通过 HTTP 能力面：上屏内容必须完全不受影响
kill_port = lambda p: [subprocess.run(["taskkill", "/F", "/PID", x], capture_output=True)
                       for x in {q.split()[4] for q in subprocess.run(["netstat", "-ano", "-p", "TCP"],
                                 capture_output=True, text=True).stdout.splitlines()
                                 if len(q.split()) >= 5 and q.split()[1].endswith(":" + str(p))
                                 and q.split()[3] == "LISTENING"}]
kill_port(PORT)
time.sleep(1)
env = dict(os.environ)
env["PAE_DB_PATH"] = str(tmpd / "t24.db")
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
    st2, ann = cap("pae.annotate", {"text": "quartz scheduler contention", "page_id": "t24-gloss"})
    glosses = [(a.get("surface"), a.get("gloss")) for a in (ann.get("result") or [])]
    res["gloss_unchanged"] = glosses
    st3, s3 = cap("pae.sensejudge", {"stats_only": True})
    res["http_stats_ok"] = (st3 == 200 and "calls_today" in (s3.get("result") or {}))
    st4, s4 = cap("pae.sensejudge", {"lemma": res["single_word"], "sentence": "The %s is ok." % res["single_word"]})
    res["http_single_sense"] = {"status": st4, "reason": (s4.get("result") or {}).get("reason")}
finally:
    eng.terminate()
    kill_port(PORT)

ok = (res.get("a1_chosen") == 1 and res.get("a1_fallback") == 0 and res.get("a1_llm_calls") == 1
      and res.get("a2_reason") in ("cache_hit_L2",) and res.get("a2_llm_calls_after") == 1
      and res.get("a3_reason") in ("single_sense", "first_is_technical")
      and res.get("a3_llm_calls_after") == 1
      and res.get("a4_fallback") == 1 and res.get("a4_chosen") is None
      and res.get("http_stats_ok")
      and res.get("gloss_unchanged") and all(g[1] for g in res["gloss_unchanged"]))
res["T24"] = "PASS" if ok else "FAIL"
(ART / "t24_sensejudge.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(res, ensure_ascii=False, indent=1))
print("T24 SENSEJUDGE:", res["T24"])