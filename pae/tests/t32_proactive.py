# -*- coding: utf-8 -*-
"""T32: 问题一修复验收——小P 从不主动说话（引擎侧没有自发 say 生产者）。

背景（实测根因）：链路 push.say → BUS → GET /v1/ext/stream(SSE) → sw.js paeBroadcast →
content.js PAE_PUSH → 角色壳 全都是通的；但引擎侧唯一会 emit say 的是能力面 pae.say
（要外部调用者）与 agent.tick（真 LLM，且 agent_schedule.enabled 默认 false）。
当时实测：两天使用量下 push_log 0 行、agent_log 0 行 → 她一次都没主动开口。

修复：push.maybe_proactive(conn, engine, now)——自发触发，由 engine.annotate 末尾
**后台线程**派发（≥300s 一次；LLM 版可能要 1-2 秒，绝不能挂在注解响应里拖慢页面）。
2026-10-03 二次反馈升级：阈值 120 次曝光/2h → 60 次/45min（实测太难撞上），
且每天最多 2 条走 LLM（带人格与近期对话上下文），失败/超额退回模板句。

本测试五段：
 A 接线：只调 engine.annotate（不碰 push），曝光够了就该自己冒出一条主动 say（轮询等后台线程）；
 B 规则矩阵：首次 130 曝光 → 发 1 条且**含词名**；再 130 曝光（未到 45min）→ 不发；
   静音标记 → 不发；解锁后按时序发满 3 条；第 4 条被「日内 3 条上限」挡住；
   评估节流：连续两次调用，第二次必须是 eval_throttled；
 C 额度：daily_say_cap 被压到 1 时，第二条走 quota 分支（④在②之前判定）；
 D 选词：最近 12 个新词不许挤掉「真的在积累」的词；
 E LLM 分支（打桩，不碰网络）：命中优先走 LLM 文本（payload.llm=1）；
   LLM 日限 2 条满了 → 第 3 条退回模板句；LLM 抛异常 → 模板句兜底。

隔离：全部跑在临时库；生产 pae.db 一行不碰。
工件：tests/artifacts/t32_proactive.json
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ART = Path(__file__).parent / "artifacts"
ART.mkdir(exist_ok=True)
TMP = Path(tempfile.mkdtemp())
os.environ["PAE_DB_PATH"] = str(TMP / "t32.db")   # 与生产库彻底隔离

from pae_core import push                              # noqa: E402
from pae_core.engine import Engine, LEMMA_PATH         # noqa: E402

# 确定性隔离：① 本测试不发真 LLM 请求（E 段用打桩）；② 先把 engine.annotate 的
# 后台派发闸门关死（间隔设成天文数字），只在 A 段显式打开——否则 B/C/D 造词时的
# annotate 会派发后台线程，与后面的直接调用产生竞态。
push.PROACTIVE_LLM_ENABLED = False
push.PROACTIVE_EVAL_INTERVAL_S = 10 ** 9

res = {"root_cause_evidence": {
    "chain": "push.say → BUS → GET /v1/ext/stream → sw.js paeBroadcast → content.js PAE_PUSH → 角色壳",
    "only_producers_before_fix": ["pae.say（能力面，需外部调用者）", "agent.tick（真 LLM，调度默认关）"],
    "observed_no_proactive": {"push_log": 0, "agent_log": 0},
}}
res["defaults"] = {"PROACTIVE_POOL_SIZE": push.PROACTIVE_POOL_SIZE,
                   "PROACTIVE_MIN_EXPOSURES": push.PROACTIVE_MIN_EXPOSURES,
                   "PROACTIVE_MIN_GAP_S": push.PROACTIVE_MIN_GAP_S,
                   "PROACTIVE_DAILY_MAX": push.PROACTIVE_DAILY_MAX,
                   "PROACTIVE_EVAL_INTERVAL_S": push.PROACTIVE_EVAL_INTERVAL_S,
                   "PROACTIVE_LLM_DAILY_MAX": push.PROACTIVE_LLM_DAILY_MAX,
                   "PROACTIVE_TEMPLATE": push.PROACTIVE_TEMPLATE}
res["defaults"]["daily_max_below_say_cap"] = (push.PROACTIVE_DAILY_MAX < 10)
res["defaults"]["thresholds_loosened"] = (push.PROACTIVE_MIN_EXPOSURES <= 60
                                          and push.PROACTIVE_MIN_GAP_S <= 45 * 60)


def seed_exposures(conn, n, ts, tag):
    """直接写 encounter 事件（等价「又翻了 n 次页面」），不折叠状态——曝光计数只读 events 表。"""
    for i in range(n):
        conn.execute("INSERT INTO events(event_id, idem_key, ts, type, payload, source)"
                     " VALUES(?,?,?,?,?,?)",
                     ("evt_%s_%d" % (tag, i), None, int(ts), "encounter",
                      json.dumps({"lemma": "quartz"}), "test"))
    conn.commit()


def proactive_rows(conn):
    return conn.execute("SELECT ts, payload FROM push_log WHERE kind='say' AND blocked=0"
                        " AND json_extract(payload,'$.proactive') = 1 ORDER BY id").fetchall()


def reset_eval():
    push._PROACTIVE_STATE["last_eval"] = 0


# ================= A 接线：annotate 自己就会说（后台线程派发） =================
res["A_wiring"] = {}
engA = Engine(str(TMP / "a.db"), LEMMA_PATH)
seed_exposures(engA.conn, 120, int(time.time()), "a")
push.PROACTIVE_EVAL_INTERVAL_S = 0        # 接线验证不受节流影响（节流单独在下面验）
reset_eval()
engA.annotate("quartz intensity", page_id="t32-wiring")
rowsA = []
for _ in range(60):                       # 后台线程派发：轮询等 say 真正落库（≤12s）
    rowsA = proactive_rows(engA.conn)
    if rowsA:
        break
    time.sleep(0.2)
a_text = json.loads(rowsA[0][1]).get("text") if rowsA else ""
res["A_wiring"] = {"rows": len(rowsA), "text": a_text,
                   "lemma": (json.loads(rowsA[0][1]).get("lemma") if rowsA else None),
                   "trigger": "engine.annotate() 末尾的后台线程派发（没有任何外部 push 调用）"}
res["A_wiring"]["annotate_alone_speaks"] = (len(rowsA) == 1)
res["A_wiring"]["text_has_word_name"] = bool(a_text) and (
    res["A_wiring"]["lemma"] in a_text if res["A_wiring"]["lemma"] else False)

# 评估节流（默认 300s）：连续两次调用，第二次必须被 eval_throttled 挡住
push.PROACTIVE_EVAL_INTERVAL_S = 300
reset_eval()
_t = int(time.time())
_first = push.maybe_proactive(engA.conn, engA, now=_t)
_second = push.maybe_proactive(engA.conn, engA, now=_t + 1)
res["A_wiring"]["throttle"] = {"first": _first.get("reason"), "second": _second.get("reason"),
                               "interval_s": 300}
res["A_wiring"]["eval_throttled_ok"] = (_second.get("reason") == "eval_throttled")

# B/C/D 造词前把派发闸门重新关死：annotate 只造词，不许再派发后台评估线程
push.PROACTIVE_EVAL_INTERVAL_S = 10 ** 9

# ================= B 规则矩阵 =================
engB = Engine(str(TMP / "b.db"), LEMMA_PATH)
for i in range(3):                                   # 造一个「最近在啃」的词：quartz 见 3 次
    engB.annotate("quartz", page_id="t32-b%d" % i)
q = engB.states.get(("quartz", 0))
day_start = int(time.time()) // 86400 * 86400
base = day_start + 6 * 3600                          # 钉在当天 06:00：所有时序都在同一自然日内
res["B_rules"] = {"quartz_encounters": (q.n_enc if q else None),
                  "quartz_s": (round(q.s_value(engB.params), 3) if q else None),
                  "band_hi": engB.params.get("band_hi"),
                  "base": base, "day_start": day_start}

push.PROACTIVE_EVAL_INTERVAL_S = 0
reset_eval()
seed_exposures(engB.conn, 130, base + 1, "b1")
r1 = push.maybe_proactive(engB.conn, engB, now=base + 60)
rowsB1 = proactive_rows(engB.conn)
t1 = json.loads(rowsB1[0][1]).get("text") if rowsB1 else ""
res["B_rules"]["r1_first_130"] = {"result": {k: v for k, v in r1.items() if k != "body"},
                                  "rows": len(rowsB1), "text": t1}
res["B_rules"]["r1_sent_one_with_word"] = (r1.get("sent") and len(rowsB1) == 1
                                          and "quartz" in t1 and t1 == "quartz 见了 3 次了，快出带了")

# 再 130 次曝光：还在 45min 间隔内 → 不发
seed_exposures(engB.conn, 130, base + 600, "b2")
r2 = push.maybe_proactive(engB.conn, engB, now=base + 660)
res["B_rules"]["r2_130_more_within_gap"] = {"reason": r2.get("reason"), "sent": r2.get("sent"),
                                            "rows": len(proactive_rows(engB.conn))}
res["B_rules"]["r2_not_sent"] = (not r2.get("sent") and len(proactive_rows(engB.conn)) == 1
                                and r2.get("reason") == "gap")

# 静音标记 → 不发（即使间隔与曝光都满足）
push.set_muted(engB.conn, True, ts=base + 700)
seed_exposures(engB.conn, 130, base + 800, "b3")
r3 = push.maybe_proactive(engB.conn, engB, now=base + 9000)
res["B_rules"]["r3_muted"] = {"reason": r3.get("reason"), "muted_flag": push.is_muted(engB.conn),
                              "rows": len(proactive_rows(engB.conn))}
res["B_rules"]["r3_muted_not_sent"] = (not r3.get("sent") and r3.get("reason") == "muted"
                                       and len(proactive_rows(engB.conn)) == 1)

# 解锁 → 第 2、3 条按时序发出；第 4 条被日内上限挡住
push.set_muted(engB.conn, False, ts=base + 9100)
seed_exposures(engB.conn, 130, base + 9200, "b4")
r4 = push.maybe_proactive(engB.conn, engB, now=base + 10800)
seed_exposures(engB.conn, 130, base + 11000, "b5")
r5 = push.maybe_proactive(engB.conn, engB, now=base + 19800)
seed_exposures(engB.conn, 130, base + 20000, "b6")
r6 = push.maybe_proactive(engB.conn, engB, now=base + 28800)
rowsB = proactive_rows(engB.conn)
res["B_rules"]["r4_sent"] = r4.get("reason")
res["B_rules"]["r5_sent"] = r5.get("reason")
res["B_rules"]["r6_reason"] = r6.get("reason")
res["B_rules"]["texts"] = [json.loads(r[1]).get("text") for r in rowsB]
res["B_rules"]["daily_max_ok"] = (r4.get("sent") and r5.get("sent") and not r6.get("sent")
                                  and r6.get("reason") == "daily_max" and len(rowsB) == 3)
res["B_rules"]["unmute_works"] = (not push.is_muted(engB.conn))

# ================= C 额度（daily_say_cap 压到 1） =================
engC = Engine(str(TMP / "c.db"), LEMMA_PATH)
engC.annotate("quartz", page_id="t32-c0")
engC.conn.execute("INSERT INTO params(key, value, updated_at, actor) VALUES('daily_say_cap','1',?,'test')",
                  (int(time.time()),))
engC.conn.commit()
seed_exposures(engC.conn, 130, base + 1, "c1")
reset_eval()
c1 = push.maybe_proactive(engC.conn, engC, now=base + 60)
seed_exposures(engC.conn, 130, base + 600, "c2")
c2 = push.maybe_proactive(engC.conn, engC, now=base + 10800)
res["C_quota"] = {"c1": {k: v for k, v in c1.items() if k != "body"}, "c2_reason": c2.get("reason"),
                  "cap_source": "params 覆盖层 daily_say_cap=1（AI 只能调低的自我约束项）"}
res["C_quota"]["quota_blocks"] = (c1.get("sent") and not c2.get("sent") and c2.get("reason") == "quota")
res["C_quota"]["rows"] = len(proactive_rows(engC.conn))

# ================= D 选词：最近 12 个新词不许挤掉「真的在积累」的词 =================
# 观察：最近 12 个词往往全是只见过 1 次的新词（每页都冒新词），而见过 ≥3 次、S 已逼近带顶的
# 词（something 4 次 0.549 / chat 8 次 0.547）会被挤出前 12 → 池子必须取 60（PROACTIVE_POOL_SIZE）。
engD = Engine(str(TMP / "d.db"), LEMMA_PATH)
for i in range(4):                                    # 一个真在积累的词：something 见 4 次
    engD.annotate("something", page_id="t32-d-s%d" % i)
time.sleep(1.2)                                       # t_E 精度是秒：隔开才叫「更晚接触」
fresh = ["analysis", "replace", "path", "value", "connect", "character",
         "welcome", "assistant", "patch", "allow", "time", "state"]
for i, w in enumerate(fresh):                         # 12 个只见过 1 次的新词，占满「最近接触」
    engD.annotate(w, page_id="t32-d-f%d" % i)
seed_exposures(engD.conn, 130, base + 1, "d1")
reset_eval()
d1 = push.maybe_proactive(engD.conn, engD, now=base + 60)
rowsD = proactive_rows(engD.conn)
d_text = json.loads(rowsD[0][1]).get("text") if rowsD else ""
res["D_pick"] = {"fresh_words_recent12": fresh, "accumulated": "something",
                 "result_lemma": d1.get("lemma"), "text": d_text,
                 "pool_size": push.PROACTIVE_POOL_SIZE}
res["D_pick"]["picks_accumulated_not_fresh"] = (d1.get("sent") and d1.get("lemma") == "something"
                                                and "something 见了 4 次了" in d_text)

# ================= E LLM 分支（打桩：不碰网络，不碰真 key） =================
res["E_llm"] = {}
push.PROACTIVE_EVAL_INTERVAL_S = 10 ** 9              # 先关派发闸门再造词
engE = Engine(str(TMP / "e.db"), LEMMA_PATH)
for i in range(3):
    engE.annotate("quartz", page_id="t32-e%d" % i)
push.PROACTIVE_LLM_ENABLED = True
push.PROACTIVE_EVAL_INTERVAL_S = 0
reset_eval()

stub_calls = []


def _stub_ok(conn, picked):
    stub_calls.append({"lemma": picked.get("lemma"), "gloss": picked.get("gloss")})
    return "这句是LLM说的：%s" % (picked.get("lemma") or "?")


push._proactive_llm_text = _stub_ok
seed_exposures(engE.conn, 130, base + 1, "e1")
e1 = push.maybe_proactive(engE.conn, engE, now=base + 60)
rowsE = proactive_rows(engE.conn)
e1_payload = json.loads(rowsE[0][1]) if rowsE else {}
res["E_llm"]["e1_llm_text"] = {"sent": e1.get("sent"), "llm_flag": e1.get("llm"),
                               "payload_llm": e1_payload.get("llm"), "text": e1_payload.get("text"),
                               "stub_saw_gloss": bool(stub_calls and stub_calls[0].get("gloss") is not None)}
res["E_llm"]["e1_uses_llm"] = (e1.get("sent") and e1.get("llm") is True
                               and e1_payload.get("llm") == 1
                               and e1_payload.get("text") == "这句是LLM说的：quartz")

# 第 2 条：间隔够 → LLM（今日 LLM 已用 1 < 2）
seed_exposures(engE.conn, 130, base + 8000, "e3")
e3 = push.maybe_proactive(engE.conn, engE, now=base + 9000)
res["E_llm"]["e3_second_llm"] = {"sent": e3.get("sent"), "llm_flag": e3.get("llm"),
                                 "text": e3.get("text")}
res["E_llm"]["e3_llm_ok"] = (e3.get("sent") and e3.get("llm") is True)

# 第 3 条：LLM 日限（2 条）已满 → 退回模板句，payload 不带 llm
seed_exposures(engE.conn, 130, base + 10000, "e4")
e4 = push.maybe_proactive(engE.conn, engE, now=base + 12000)
res["E_llm"]["e4_llm_daily_cap"] = {"sent": e4.get("sent"), "llm_flag": e4.get("llm"),
                                    "text": e4.get("text")}
res["E_llm"]["e4_falls_back_to_template"] = (e4.get("sent") and not e4.get("llm")
                                             and e4.get("text") == "quartz 见了 3 次了，快出带了")

# LLM 抛异常 → 模板句兜底（新引擎：LLM 配额是 0/2）
engE2 = Engine(str(TMP / "e2.db"), LEMMA_PATH)
for i in range(3):
    engE2.annotate("quartz", page_id="t32-e2-%d" % i)
reset_eval()


def _stub_boom(conn, picked):
    raise RuntimeError("llm down")


push._proactive_llm_text = _stub_boom
seed_exposures(engE2.conn, 130, base + 1, "e2")
e5 = push.maybe_proactive(engE2.conn, engE2, now=base + 60)
res["E_llm"]["e5_llm_error_fallback"] = {"sent": e5.get("sent"), "llm_flag": e5.get("llm"),
                                         "text": e5.get("text")}
res["E_llm"]["e5_template_on_error"] = (e5.get("sent") and not e5.get("llm")
                                        and e5.get("text") == "quartz 见了 3 次了，快出带了")
push.PROACTIVE_LLM_ENABLED = False

ok = (res["defaults"]["daily_max_below_say_cap"]
      and res["defaults"]["thresholds_loosened"]
      and res["A_wiring"]["annotate_alone_speaks"] and res["A_wiring"]["text_has_word_name"]
      and res["A_wiring"]["eval_throttled_ok"]
      and res["B_rules"]["r1_sent_one_with_word"] and res["B_rules"]["r2_not_sent"]
      and res["B_rules"]["r3_muted_not_sent"] and res["B_rules"]["daily_max_ok"]
      and res["B_rules"]["unmute_works"] and res["C_quota"]["quota_blocks"]
      and res["D_pick"]["picks_accumulated_not_fresh"]
      and res["E_llm"]["e1_uses_llm"] and res["E_llm"]["e3_llm_ok"]
      and res["E_llm"]["e4_falls_back_to_template"] and res["E_llm"]["e5_template_on_error"])
res["T32"] = "PASS" if ok else "FAIL"
(ART / "t32_proactive.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(res, ensure_ascii=False, indent=1))
print("T32 PROACTIVE:", res["T32"])
sys.exit(0 if ok else 1)
