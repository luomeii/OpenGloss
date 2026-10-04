# -*- coding: utf-8 -*-
"""PAE 智能体运行时：让内置角色真的「思考并行动」——补上缺失的「神经系统」。

dogfooding 纪律（审查方要求 + 36 号文档记录）：
内置 companion 与外部智能体走**同一套能力、同一套守门、同一份配额、同一份审计**，
唯一差别是 in-process 调用省掉 key 解析（actor_hint 接缝），其余一个都不少。

本模块只做三件事：① 组装决策提示（人格 + 状态 + 可用能力）② 解析模型给的 JSON 计划
③ 把 actions 交给 capsurface 执行，并把「想了什么/做了什么」写进 agent_log（透明）。
"""
import json
import re
import time

from . import capsurface
from . import llm as llm_mod

MAX_ACTIONS_PER_TICK = 3
TICK_MIN_INTERVAL_S = 15      # 两次 tick 至少间隔（每次 tick 都是一次真实 LLM 调用）
TICK_DAILY_LIMIT = 100        # 每日上限（防失控烧额度）

DECISION_TAIL = """

# 你现在要做的事
看下面给你的状态与指标，决定**这一轮要不要做点什么**。上面列出的能力你可以调用。

判断依据优先看 outcomes.conversion_rate（结果侧北极星：至少 7 天前被注解过的词里，后来真的点过「认识了」的比例）。
这个数字不涨，说明注解只是噪音——那种情况下**降温比加码更有用**。

只在确有必要时行动（宁可什么都不做）：
- 想改某个变量（注解太密/太稀、先验不对等）→ pae.propose，必须给 rationale，且只能用上面列出的可调变量
- 想对他说一句话 → pae.say（每天有上限，别浪费）
- 想记住一件事 → pae.remember（必须带 evidence）
- 什么都不需要做 → actions 留空数组

绝对不要：
- 提问（尤其不要问「你学会了吗」）
- 说教、展开语法讲解
- 为了显得有用而行动

# 能力调用示例（**照这个结构写**，patch 必须是数组，每项 {key, to}）
{"thought": "注解密度偏高，先降低门槛观察一周", "actions": [{"name": "pae.propose",
 "params": {"patch": [{"key": "annotate_max_s", "to": 0.6}],
            "rationale": "近7日曝光多但悬停率低，注解里混进了已认识的词",
            "evidence": ["pae.metrics"], "ttl_days": 7}}]}

{"thought": "他没在页面上，也没什么可说的", "actions": []}

注意：propose 只能用上面 tunable_variables 里列出的键，且必须在给定区间内；越界会被守门拒绝。

只输出 JSON，不要解释、不要 markdown 代码块：
{"thought": "一句话说明你为什么这么判断", "actions": []}
"""


def tool_doc():
    """可用能力清单（只列真能调用的：kind=act/inform 且 status=implemented）。"""
    m = capsurface.load_manifest()
    out = []
    for c in m.get("capabilities") or []:
        if c.get("kind") not in ("act", "inform"):
            continue
        if c.get("status") != "implemented":
            continue
        out.append({"name": c.get("name"), "description": c.get("description"),
                    "params": c.get("params") or {}})
    return out


def _parse(text):
    t = (text or "").strip()
    t = re.sub(r"^\s*```(?:json)?|```\s*$", "", t, flags=re.M).strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    m = re.search(r"\{.*\}", t, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


def observe():
    """通过能力面取上下文（与外部智能体同一条路）。"""
    status, body = capsurface.handle("pae.context", {"top_n": 30}, actor_hint="companion")
    return (body or {}).get("result") or {}


def tunable_doc(conn):
    """可调变量表：当前值 + 区间 + 默认值（模型必须看到这个才提得出合法提案）。"""
    from . import params as params_mod
    eff = params_mod.effective(conn)
    return {k: {"current": eff.get(k), "range": list(params_mod.RANGE_ALL.get(k) or []),
                "default": params_mod.DEFAULTS_ALL.get(k)}
            for k in params_mod.TUNABLE}


def decide(ctx, stimulus="", conn=None):
    sys_prompt = ((ctx.get("persona") or {}).get("prompt") or "") + DECISION_TAIL
    payload = {
        "state": {"learner": ctx.get("learner"), "limits": ctx.get("limits")},
        "metrics": ctx.get("metrics"),            # 投入侧：系统做了什么
        "outcomes": ctx.get("outcomes"),          # 结果侧：有没有用（conversion_rate 是北极星）
        "words_in_band_top15": (ctx.get("words") or [])[:15],
        "page": ctx.get("page"),
        "your_own_state": ctx.get("self"),   # 你最近想过什么/说过几句/已经改了什么（别重复）
        "memory": ctx.get("memory"),          # 你记住的事 + 最近对话（开口前先看这块）
        "tunable_variables": tunable_doc(conn) if conn is not None else {},
        "capabilities": tool_doc(),
    }
    user = stimulus or ("当前状态（JSON）：\n" + json.dumps(payload, ensure_ascii=False)[:6000])
    r = llm_mod.chat([{"role": "system", "content": sys_prompt},
                      {"role": "user", "content": user}], temperature=0.4, timeout=120)
    return r["content"], r


def _repair(action, status, body, ctx, conn):
    """把守门的拒绝理由原样回喂，让模型改一次参数（R10 的「结构化提示让模型自修」）。"""
    try:
        ask = ("你上一次调用被拒绝了，请修正后**只输出一个 JSON 对象**（形如 "
               '{"name":"...","params":{...}}），不要解释。\n'
               "你上次给的：%s\n拒绝理由：%s\n可调变量：%s"
               % (json.dumps(action, ensure_ascii=False), json.dumps(body, ensure_ascii=False),
                  json.dumps(tunable_doc(conn), ensure_ascii=False)))
        r = llm_mod.chat([{"role": "system", "content": "你是严格的参数修正器，只输出 JSON。"},
                          {"role": "user", "content": ask[:4000]}], temperature=0.2, timeout=60)
        out = _parse(r["content"])
        if isinstance(out, dict) and out.get("name"):
            return out
    except Exception:
        pass
    return None


def tick(conn, actor="companion", stimulus=""):
    """跑一轮：观察 → 判断 → 行动 → 留痕。返回本轮记录。"""
    t0 = time.time()
    # 限流：tick 会真实花 LLM 额度，不能无限跑
    now_i = int(time.time())
    day_start = now_i // 86400 * 86400
    row = conn.execute("SELECT MAX(ts) FROM agent_log").fetchone()
    last_ts = (row or [None])[0]
    if last_ts and now_i - last_ts < TICK_MIN_INTERVAL_S:
        # 被限流也留痕（用户能看见「它想跑但被节流了」），但不吃每日额度
        conn.execute("INSERT INTO agent_log(ts, actor, stimulus, thought, actions, result, elapsed_ms)"
                     " VALUES(?,?,?,?,?,?,?)",
                     (now_i, actor, (stimulus or "")[:500], "(rate_limited)",
                      "[]", json.dumps({"error": "rate_limited"}), 0))
        conn.commit()
        return {"actor": actor, "thought": "", "actions": [], "error": "rate_limited",
                "detail": "两次 tick 至少间隔 %ds（防烧额度）" % TICK_MIN_INTERVAL_S,
                "wait_s": TICK_MIN_INTERVAL_S - (now_i - last_ts), "elapsed_ms": 0}
    today_ticks = conn.execute(
        "SELECT COUNT(*) FROM agent_log WHERE ts >= ? AND result NOT LIKE '%rate_limited%'",
        (day_start,)).fetchone()[0]
    if today_ticks >= TICK_DAILY_LIMIT:
        return {"actor": actor, "thought": "", "actions": [], "error": "daily_limit",
                "detail": "今日 tick 已达上限 %d" % TICK_DAILY_LIMIT, "elapsed_ms": 0}
    ctx = observe()
    raw, meta = ("", {})
    actions = []
    results = []
    thought = ""
    err = None
    try:
        raw, meta = decide(ctx, stimulus, conn=conn)
        plan = _parse(raw)
        if plan is None:
            err = "bad_json"
        else:
            thought = str(plan.get("thought") or "")[:500]
            for a in (plan.get("actions") or [])[:MAX_ACTIONS_PER_TICK]:
                if not isinstance(a, dict) or not a.get("name"):
                    continue
                st, body = capsurface.handle(a["name"], a.get("params") or {}, actor_hint=actor)
                results.append({"name": a["name"], "status": st, "body": body})
                # 失败给一次自修机会（把错误原样回喂，让模型改参数，最多一次）
                if st >= 400:
                    fixed = _repair(a, st, body, ctx, conn)
                    if fixed:
                        st2, body2 = capsurface.handle(fixed["name"], fixed.get("params") or {},
                                                       actor_hint=actor)
                        results.append({"name": fixed["name"], "status": st2, "body": body2,
                                        "repaired": True})
    except Exception as e:
        err = "%s: %s" % (type(e).__name__, str(e)[:200])
    elapsed = int((time.time() - t0) * 1000)
    conn.execute(
        "INSERT INTO agent_log(ts, actor, stimulus, thought, actions, result, elapsed_ms, raw_reply)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (int(time.time()), actor, (stimulus or "")[:500], thought,
         json.dumps(actions, ensure_ascii=False), json.dumps({"results": results, "error": err}, ensure_ascii=False),
         elapsed, (raw or "")[:2000]))
    conn.commit()
    return {"actor": actor, "thought": thought, "actions": results, "error": err,
            "elapsed_ms": elapsed, "usage": (meta or {}).get("usage")}


def log(conn, n=20):
    out = []
    for r in conn.execute("SELECT ts, actor, stimulus, thought, result, elapsed_ms FROM agent_log"
                          " ORDER BY id DESC LIMIT ?", (int(n),)):
        try:
            res = json.loads(r[4] or "{}")
        except Exception:
            res = {}
        out.append({"ts": r[0], "actor": r[1], "stimulus": r[2], "thought": r[3],
                    "result": res, "elapsed_ms": r[5]})
    return out