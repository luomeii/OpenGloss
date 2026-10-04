# -*- coding: utf-8 -*-
"""T31: 首日反馈修复验收（2026-10-02）——① 多轮上下文 ②「最近在啃」排序。

① pae.persona.say 现在把最近 6 轮真对话作为**消息数组**传给 LLM（不再只塞在背景资料里）。
   验收：连续两次开口——第一次自报姓名，第二次问「我刚才说我叫什么名字？」，
   第二次回复里必须出现「小安」。需要真实 LLM（temperature=0），**不进 nightly**，手动跑：
       $env:HTTPS_PROXY='http://127.0.0.1:<你的代理端口>'; python tests/t31_context.py
② observe.band_words 默认按 last_seen 降序（最近接触在前）；order='s' 保留旧的 S 升序。

工件：tests/artifacts/t31_context.json
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

# 隔离：整个测试跑在临时库上，绝不碰生产 pae.db
TMP = Path(tempfile.mkdtemp())
os.environ["PAE_DB_PATH"] = str(TMP / "t31.db")

from pae_core import capsurface                      # noqa: E402
from pae_core import llm as llm_mod                  # noqa: E402
from pae_core import observe as obs_mod              # noqa: E402
from pae_core.engine import get_engine               # noqa: E402

res = {"bug1": {}, "bug2": {}}
eng = get_engine()

# ================= Bug②：最近在啃 = 按最后接触时间 =================
A, B = "contention", "quartz"
eng.annotate(A, page_id="t31-a")            # 先遇
time.sleep(1.2)                             # t_E 精度是秒：隔开一秒才叫「先后」
eng.annotate(B, page_id="t31-b1")           # 后遇（两次遭遇 → S 更高）
eng.annotate(B, page_id="t31-b2")
st_a, st_b = eng.states[(A, 0)], eng.states[(B, 0)]
s_a, s_b = st_a.s_value(eng.params), st_b.s_value(eng.params)
hi = eng.params.get("band_hi", 0.55)
default_list = obs_mod.band_words(eng)
s_order_list = obs_mod.band_words(eng, order="s")
res["bug2"] = {
    "A": A, "B": B,
    "a_s": round(s_a, 3), "b_s": round(s_b, 3), "band_hi": hi,
    "a_last_seen": st_a.t_E, "b_last_seen": st_b.t_E,
    "both_in_band": bool(s_a <= hi and s_b <= hi),
    "default_order": [w["lemma"] for w in default_list],
    "default_first": default_list[0]["lemma"] if default_list else None,
    "order_s": [w["lemma"] for w in s_order_list],
    "order_s_first": s_order_list[0]["lemma"] if s_order_list else None,
}
res["bug2"]["recent_desc_ok"] = (res["bug2"]["b_last_seen"] > res["bug2"]["a_last_seen"]
                                 and res["bug2"]["default_first"] == B)
res["bug2"]["s_asc_ok"] = (res["bug2"]["order_s_first"] == A
                           and [w["s_value"] for w in s_order_list] == sorted(w["s_value"] for w in s_order_list))

# ================= Bug①：多轮上下文（真 LLM，temperature=0） =================
# 顺带当探针：记录每次真实送出的 messages 角色序列（证明历史是「消息」而不是背景文字）
_calls = []
_orig_chat = llm_mod.chat


def _spy(messages, **kw):
    _calls.append({"n": len(messages), "roles": [m.get("role") for m in messages],
                   "temperature": kw.get("temperature")})
    return _orig_chat(messages, **kw)


llm_mod.chat = _spy
try:
    st1, b1 = capsurface.handle("pae.persona.say",
                                {"stimulus": "我叫小安，记住我叫小安", "temperature": 0},
                                actor_hint="local")
    r1 = ((b1 or {}).get("result") or {})
    st2, b2 = capsurface.handle("pae.persona.say",
                                {"stimulus": "我刚才说我叫什么名字？", "temperature": 0},
                                actor_hint="local")
    r2 = ((b2 or {}).get("result") or {})
    res["bug1"] = {
        "status": [st1, st2],
        "call1_stimulus": "我叫小安，记住我叫小安",
        "call1_reply": r1.get("reply", ""),
        "call2_stimulus": "我刚才说我叫什么名字？",
        "call2_reply": r2.get("reply", ""),
        "history_turns_call1": r1.get("history_turns"),
        "history_turns_call2": r2.get("history_turns"),
        "messages_seen": _calls,
        "model": r2.get("model") or r1.get("model"),
        "errors": [((b1 or {}).get("error")), ((b2 or {}).get("error"))],
    }
    res["bug1"]["recall_ok"] = ("小安" in (r2.get("reply") or ""))
    res["bug1"]["history_passed_as_messages"] = any(
        c["roles"] == ["system", "user", "assistant", "user"] for c in _calls)
    res["bug1"]["temperature_zero_used"] = all(c["temperature"] == 0 for c in _calls)
except Exception as e:   # 无 key / 无代理：如实记录，不吞
    res["bug1"] = {"exception": "%s: %s" % (type(e).__name__, e), "recall_ok": False,
                   "history_passed_as_messages": False, "temperature_zero_used": False,
                   "messages_seen": _calls}
finally:
    llm_mod.chat = _orig_chat

# 顺带复查印章卡数据链：会话轮次现在真的落库了（facts 仍为空 → 兜底 recent 有货）
turns = eng.conn.execute("SELECT role, text FROM chat_turns ORDER BY ts, rowid").fetchall()
res["stamp_chain"] = {
    "chat_turns_rows": len(turns),
    "first_turn": ({"role": turns[0][0], "text": turns[0][1][:40]} if turns else None),
    "sw_reads": ["text", "kind", "confidence", "role"],
    "note": "facts 无写入方时 SW 退回 memory.recent；字段名 text/role 与引擎返回一致",
}

ok = bool(res["bug2"]["recent_desc_ok"] and res["bug2"]["s_asc_ok"]
          and res["bug2"]["both_in_band"]
          and res["bug1"].get("recall_ok") and res["bug1"].get("history_passed_as_messages")
          and res["bug1"].get("temperature_zero_used")
          and res["stamp_chain"]["chat_turns_rows"] >= 2)
res["T31"] = "PASS" if ok else "FAIL"
(ART / "t31_context.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(res, ensure_ascii=False, indent=1))
print("T31 CONTEXT:", res["T31"])
sys.exit(0 if ok else 1)
