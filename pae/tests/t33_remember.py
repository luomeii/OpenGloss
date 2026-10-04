# -*- coding: utf-8 -*-
"""T33: 第二天第二次反馈修复验收（2026-10-03）——① [[记住]] 长期记忆协议 ② 长上下文。

背景（用户实测两条硬伤）：
 ① 聊天里让她「记下这两句话」，6-7 句后让她回忆，她不知道——整条链路没有任何
    生产者写 facts，侧栏「它记下的」一直是拿最近 4 条对话凑数的假象（旧 SW 兜底）。
    修复：persona.say 给模型的系统提示里加内联协议——用户让记时，回复末尾带
    [[记住]] 行；引擎截走这一行 → 真写 facts（带证据、当日限流 8 条）→
    下一次 say 自动注入「我记得：…」→ 侧栏印章卡显示真事实。
 ② 聊约 30 轮后她连用户第一句都忘了——历史窗口只有 6 轮。
    修复：历史窗口按 config chat_history_turns（默认 64 轮）整段送入。

本测试全部打桩 LLM（不碰网络/真 key），可进 nightly。
工件：tests/artifacts/t33_remember.json
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ART = Path(__file__).parent / "artifacts"
ART.mkdir(exist_ok=True)
TMP = Path(tempfile.mkdtemp())
os.environ["PAE_DB_PATH"] = str(TMP / "t33.db")   # 与生产库彻底隔离

from pae_core import capsurface                      # noqa: E402
from pae_core import llm as llm_mod                  # noqa: E402
from pae_core import memory as mem_mod               # noqa: E402
from pae_core.engine import get_engine               # noqa: E402

res = {}
eng = get_engine()
_captured = []
_orig_chat = llm_mod.chat

HIST_WINDOW = 64   # 与 config.json 默认一致


def _fake_reply(content):
    def _chat(messages, **kw):
        _captured.append([dict(m) for m in messages])
        return {"content": content, "reasoning": "", "usage": {}, "model": "stub"}
    return _chat


def _say(stimulus):
    st, body = capsurface.handle("pae.persona.say", {"stimulus": stimulus, "temperature": 0},
                                 actor_hint="local")
    return st, ((body or {}).get("result") or {})


# ================= ① 协议：标记行被截走 → 真写 facts =================
llm_mod.chat = _fake_reply("好的，我记住了。\n[[记住]] 用户叫小安")
st1, r1 = _say("我叫小安，记住我叫小安")
facts_after1 = mem_mod.active_facts(eng.conn)
res["protocol_write"] = {
    "status": st1, "reply": r1.get("reply"), "remembered": r1.get("remembered"),
    "facts_rows": len(facts_after1),
    "fact_texts": [f.get("text") for f in facts_after1],
    "fact_evidence_nonempty": all(bool(f.get("evidence")) for f in facts_after1),
}
res["protocol_write"]["marker_stripped"] = ("[[记住]]" not in (r1.get("reply") or ""))
res["protocol_write"]["reply_kept_body"] = ("好的，我记住了。" == r1.get("reply"))
res["protocol_write"]["fact_written"] = (len(facts_after1) == 1
                                         and facts_after1[0].get("text") == "用户叫小安"
                                         and facts_after1[0].get("source_actor") == "companion"
                                         and facts_after1[0].get("kind") == "event")
res["protocol_write"]["remembered_ok"] = bool((r1.get("remembered") or {}).get("ok"))

# ================= ② 模型只回了标记行 → 兜底「记下了。」且事实照写 =================
llm_mod.chat = _fake_reply("[[记住]] 用户讨厌长篇大论")
st2, r2 = _say("另外记住：我讨厌长篇大论")
facts_after2 = mem_mod.active_facts(eng.conn)
res["marker_only"] = {"reply": r2.get("reply"), "facts_rows": len(facts_after2),
                      "texts": [f.get("text") for f in facts_after2]}
res["marker_only"]["fallback_reply"] = (r2.get("reply") == "记下了。")
# 两条事实可能同一秒写入（created_at 秒级，排序不保证先后）→ 用集合比较，不赌顺序
res["marker_only"]["fact_written"] = (len(facts_after2) == 2
                                      and sorted(f.get("text") for f in facts_after2)
                                          == sorted(["用户叫小安", "用户讨厌长篇大论"]))

# ================= ③ 事实注入：下一次 say 的系统提示里必须出现「我记得：…」 =================
llm_mod.chat = _fake_reply("嗯嗯。")
st3, r3 = _say("你觉得我们聊到哪了？")
sys_msgs = [m for m in (_captured[-1] if _captured else []) if m.get("role") == "system"]
sys_text = "\n".join(str(m.get("content")) for m in sys_msgs)
res["fact_injection"] = {"system_msgs": len(sys_msgs),
                         "has_name_fact": ("用户叫小安" in sys_text),
                         "has_hate_fact": ("用户讨厌长篇大论" in sys_text),
                         "inject_prefix_seen": ("我记得：" in sys_text)}
res["fact_injection"]["injected"] = (res["fact_injection"]["has_name_fact"]
                                     and res["fact_injection"]["has_hate_fact"])

# ================= ④ 长上下文：80 轮历史 → 只送最近 64 轮，最老的 16 轮不出现 =================
sid = mem_mod.session_open(eng.conn, "companion")["session_id"]
for i in range(1, 81):
    role = "user" if i % 2 else "agent"
    mem_mod.session_turn(eng.conn, sid, role, "turn%03d 这是第%d轮" % (i, i))
llm_mod.chat = _fake_reply("嗯。")
st4, r4 = _say("我们第一轮聊了什么？")
msgs4 = _captured[-1] if _captured else []
all_text = "\n".join(str(m.get("content")) for m in msgs4)
roles4 = [m.get("role") for m in msgs4]
res["long_history"] = {
    "history_turns": r4.get("history_turns"),
    "messages_total": len(msgs4),
    "roles_head": roles4[:3],
    "has_oldest_kept": ("turn017" in all_text),      # 80+4 轮里最后 64 轮 → turn017..turn080
    "has_latest": ("turn080" in all_text),
    "dropped_oldest": (("turn001" not in all_text) and ("turn016" not in all_text)),
}
res["long_history"]["window_ok"] = (r4.get("history_turns") == HIST_WINDOW
                                    and len(msgs4) == HIST_WINDOW + 2
                                    and res["long_history"]["has_oldest_kept"]
                                    and res["long_history"]["has_latest"]
                                    and res["long_history"]["dropped_oldest"])

# ================= ⑤ 意图兜底（2026-10-03 三次反馈）：模型不写标记行也能记 =================
# 实测发现：使用者说「记住 XX」时，LLM 口头答应但从不写
# [[记住]] 标记行 → facts 恒 0。引擎侧确定性 regex 捕获用户原话写库（source_actor=user）。
llm_mod.chat = _fake_reply("好，我记住了。")          # 无标记行
st5, r5 = _say("记住暗号")
facts5 = mem_mod.active_facts(eng.conn)
res["intent_capture"] = {"reply": r5.get("reply"), "remembered": r5.get("remembered"),
                         "texts": [f.get("text") for f in facts5]}
res["intent_capture"]["plain_remember"] = (
    (r5.get("remembered") or {}).get("ok") is True
    and (r5.get("remembered") or {}).get("via") == "intent"
    and "暗号" in [f.get("text") for f in facts5])

llm_mod.chat = _fake_reply("嗯嗯，记好了。")           # 冒号变体 + 带句尾
st6, r6 = _say("帮我记住：代号是 k7x9")
facts6 = mem_mod.active_facts(eng.conn)
res["intent_capture"]["colon_variant"] = (
    (r6.get("remembered") or {}).get("via") == "intent"
    and "代号是 k7x9" in [f.get("text") for f in facts6])

llm_mod.chat = _fake_reply("这个我不熟。")             # 否定语境不许误写
st7, r7 = _say("这个词我没记住，怎么办")
facts7 = mem_mod.active_facts(eng.conn)
res["intent_capture"]["negative_no_write"] = (
    (r7.get("remembered") is None)
    and len(facts7) == len(facts6))

llm_mod.chat = _fake_reply("嗯。")                     # 闲聊不写
st8, r8 = _say("今天天气不错")
res["intent_capture"]["chitchat_no_write"] = (r8.get("remembered") is None)

# M1 加固（评审 2026-10-03）：此前实测全部误捕的形态，现在必须 None
llm_mod.chat = _fake_reply("嗯。")
for _bad_stim in ("没有记住这件事", "不要记住这个密码", "我记下的东西越来越多了",
                  "记住这个词怎么写"):
    _st, _r = _say(_bad_stim)
    res["intent_capture"]["bad_" + _bad_stim[:6]] = (_r.get("remembered") is None)
_facts_end = mem_mod.active_facts(eng.conn)
res["intent_capture"]["m1_hardening"] = (len(_facts_end) == len(facts7)
                                         and all(res["intent_capture"]["bad_" + k[:6]]
                                                 for k in ("没有记住这件事", "不要记住这个密码",
                                                           "我记下的东西越来越多了", "记住这个词怎么写")))

llm_mod.chat = _orig_chat

ok = (res["protocol_write"]["marker_stripped"] and res["protocol_write"]["reply_kept_body"]
      and res["protocol_write"]["fact_written"] and res["protocol_write"]["remembered_ok"]
      and res["protocol_write"]["fact_evidence_nonempty"]
      and res["marker_only"]["fallback_reply"] and res["marker_only"]["fact_written"]
      and res["fact_injection"]["injected"]
      and res["long_history"]["window_ok"]
      and res["intent_capture"]["plain_remember"]
      and res["intent_capture"]["colon_variant"]
      and res["intent_capture"]["negative_no_write"]
      and res["intent_capture"]["chitchat_no_write"]
      and res["intent_capture"]["m1_hardening"])
res["T33"] = "PASS" if ok else "FAIL"
(ART / "t33_remember.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(res, ensure_ascii=False, indent=1))
print("T33 REMEMBER:", res["T33"])
sys.exit(0 if ok else 1)
