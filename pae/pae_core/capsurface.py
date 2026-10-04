# -*- coding: utf-8 -*-
"""PAE 能力面：一份清单（capabilities.json）+ 鉴权 + 审计 + 分发。

内置 companion 与外部智能体走同一入口，谁都没有内部捷径（防两条路径漂移）。
"""
import json
import time
from pathlib import Path

from . import keys as keys_mod

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "capabilities.json"
CONFIG_PATH = ROOT / "config.json"


def load_manifest() -> dict:
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"capabilities": [], "version": "0"}


def summary(m: dict = None) -> dict:
    m = m or load_manifest()
    caps = m.get("capabilities") or []
    impl = [c["name"] for c in caps if c.get("status") == "implemented"]
    stub = [c["name"] for c in caps if c.get("status") == "stub"]
    planned = [c["name"] for c in caps if c.get("status") == "planned"]
    return {
        "version": m.get("version"),
        "total": len(caps),
        "implemented_count": len(impl),
        "stub_count": len(stub),
        "planned_count": len(planned),
        "implemented": impl,
        "stub": stub,
        "planned": planned,
        "status_legend": {"implemented": "真功能", "stub": "有壳但如实回答「没这功能」",
                          "planned": "只有设计，调用返回 501"},
        "auth": m.get("auth"),
        "transports": m.get("transports"),
        "note": m.get("note"),
    }


def manifest_doc() -> dict:
    """给外部智能体读的自描述清单（含实现状态，避免把图纸当房子）。"""
    m = load_manifest()
    return {"summary": summary(m), "capabilities": m.get("capabilities") or []}


def find(name: str):
    for c in load_manifest().get("capabilities") or []:
        if c.get("name") == name:
            return c
    return None


def _fam_mode() -> str:
    """熟悉度投影模式：off / shadow（默认，零行为变化）/ enforce（真的跳过）。"""
    try:
        c = _config()
        m = str(c.get("familiarity_mode") or "shadow").lower()
        return m if m in ("off", "shadow", "enforce") else "shadow"
    except Exception:
        return "shadow"


def _config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


# 审计失败必须留痕（静默 catch 已经坑过两次：注入函数作用域、审计连接）
LAST_AUDIT_ERROR = None


def audit(actor: str, capability: str, ok: bool, detail: str = "") -> None:
    """审计：谁在什么时候动了哪项能力（用户要能看见「哪个智能体在动我的系统」）。

    审计失败不拖垮主流程，但错误必须落进 LAST_AUDIT_ERROR，供测试与排障读取。
    """
    global LAST_AUDIT_ERROR
    try:
        from .db import log_call
        from .engine import get_engine
        log_call(get_engine().conn, actor, capability, ok, detail[:300])
        LAST_AUDIT_ERROR = None
    except Exception as e:
        LAST_AUDIT_ERROR = "%s: %s" % (type(e).__name__, e)


def _remember_turns(conn, user_text: str, agent_text: str):
    """把一次「说出来」的对话写成会话轮次（chat_turns）。

    2026-10-02 首日反馈复查发现：整条产品链路上**没有任何地方写 chat_turns**——
    扩展对话路径只调 pae.persona.say，从不调 pae.session.turn，所以 recent_turns 恒为空，
    多轮上下文没有可传的历史（也顺带让印章卡的「它答/你说」兜底数据永远是空的）。
    这里补上唯一缺的那一步写：复用最新未关闭的 companion 会话，没有就开一个。
    相邻重复写入会被跳过（同一句重复调 say 不会灌水）。
    """
    from . import memory as mem_mod
    row = conn.execute("SELECT session_id FROM chat_sessions WHERE actor='companion'"
                       " AND ended_at IS NULL ORDER BY started_at DESC LIMIT 1").fetchone()
    sid = row[0] if row else mem_mod.session_open(conn, "companion")["session_id"]
    for role, text in (("user", user_text), ("agent", agent_text)):
        text = str(text or "").strip()
        if not text:
            continue
        last = conn.execute("SELECT role, text FROM chat_turns WHERE session_id=?"
                            " ORDER BY ts DESC, rowid DESC LIMIT 1", (sid,)).fetchone()
        if last and last[0] == role and last[1] == text:
            continue
        mem_mod.session_turn(conn, sid, role, text)
    return sid


def _extract_remember_intent(text):
    """从用户话里确定性提取「让记的内容」（不依赖模型自觉）。

    背景（2026-10-03 第三次反馈）：[[记住]] 标记协议靠模型配合，实测 deepseek-flash
    不稳定——使用者明确让她记时，她口头说记了但从不写
    标记行 → facts 恒 0，侧栏印章卡永远空。这里做引擎侧兜底：识别用户话里的
    「记住X / 记一下X / 记个X / 别忘了X」，取后面的内容当事实文本。
    提取的是**用户原话**，比模型转述更忠实；写成 source_actor='user'（受保护，
    AI 主动淘汰时不动用户的事实）。
    """
    import re as _re
    t = str(text or "")
    strip = ' 	:：,，.。!！?？;；了啦呀哦啊「」“”‘’'
    NEG = ("没", "别", "不", "曾", "未", "莫", "勿")
    ASK = ("怎么", "吗", "如何", "什么", "为什么", "？", "?", "咋")

    def _clean(seg):
        return seg.strip().strip(strip).strip()

    def _negated(start):
        # M1 加固（评审 2026-10-03）：「没有/不要」两字否定此前漏拦（只查单字）。
        # 前两字窗口内含任意否定字即视为否定语境。
        w = t[max(0, start - 2):start]
        return any(c in NEG for c in w)

    def _question(seg):
        # 问句不存：用户在**问**怎么记住/怎么写，不是在**让**记。
        return any(q in seg for q in ASK)

    for m in _re.finditer(r"记住", t):
        if _negated(m.start()):
            continue
        seg = _clean(t[m.end():])
        if 1 < len(seg) <= 80 and not _question(seg):
            return seg[:80]
    for m in _re.finditer(r"记(一下|个|下)", t):
        if _negated(m.start()):
            continue
        # M1 加固：记(下|个) 的左邻必须是发起词（帮/请/你/我/就/先/再/快/来/给）；
        # 且「记下的…」是名词化陈述句（「我记下的东西」），跟在后面的『的』直接排除。
        pre = t[max(0, m.start() - 1):m.start()]
        if pre not in ("帮", "请", "你", "我", "就", "先", "再", "快", "来", "给"):
            continue
        if t[m.end():m.end() + 1] == "的":
            continue
        seg = _clean(t[m.end():])
        if 1 < len(seg) <= 80 and not _question(seg):
            return seg[:80]
    m = _re.search(r"别忘了", t)
    if m:
        seg = _clean(t[m.end():])
        if 1 < len(seg) <= 80 and not _question(seg):
            return seg[:80]
    return None


def _dispatch(name: str, params: dict, actor: str = "local"):
    from .engine import get_engine
    from . import llm as llm_mod
    from . import persona as persona_mod

    if name == "pae.health":
        return {"ok": True}
    if name == "pae.annotate":
        return get_engine().annotate(params.get("text", ""), params.get("page_id") or "default",
                                     params.get("session_id"))
    if name == "pae.status":
        return get_engine().status()
    if name == "pae.config":
        return _config()
    if name == "pae.event":
        metas = {k: params.get(k) for k in ("page_id", "session_id", "s_value", "page_active_ms")
                 if params.get(k) is not None}
        return get_engine().record_event(params.get("type"), params.get("lemma"),
                                         params.get("surface") or "", metas)
    if name == "pae.context":
        from . import observe as obs_mod
        return obs_mod.assemble(get_engine(), params or {})
    if name == "pae.page":
        from . import observe as obs_mod
        return obs_mod.page(get_engine(), params or {})
    if name == "pae.metrics":
        from . import metrics as metrics_mod
        rng = params.get("range") or ""
        if isinstance(rng, str) and rng.endswith("d"):
            days = int(rng[:-1] or 7)
        else:
            days = int(params.get("days") or 7)
        return metrics_mod.compute(get_engine().conn, days=days)
    if name == "pae.word":
        from . import observe as obs_mod
        return obs_mod.word(get_engine(), params.get("lemma") or "")
    if name == "pae.events":
        from . import observe as obs_mod
        return obs_mod.events(get_engine(), params or {})
    if name == "pae.audit":
        from . import observe as obs_mod
        return obs_mod.decisions(get_engine(), params or {})
    if name in ("pae.memory.search", "pae.memory.recent", "pae.remember", "pae.forget",
                "pae.session.open", "pae.session.turn", "pae.session.close"):
        from . import memory as mem_mod
        conn = get_engine().conn
        if name == "pae.memory.search":
            return mem_mod.search(conn, params.get("query") or "", int(params.get("k") or 5),
                                  params.get("kind"))
        if name == "pae.memory.recent":
            return mem_mod.recent(conn, params.get("session_id"), int(params.get("n") or 10))
        if name == "pae.remember":
            ok, body = mem_mod.remember(conn, params.get("actor") or "companion",
                                        params.get("kind"), params.get("text"),
                                        params.get("evidence"), params.get("confidence", 0.7),
                                        params.get("ttl_days"))
            if not ok:
                raise ValueError(json.dumps(body, ensure_ascii=False))
            return body
        if name == "pae.forget":
            ok, body = mem_mod.forget(conn, params.get("actor") or "companion", params.get("fact_id"))
            if not ok:
                raise ValueError(json.dumps(body, ensure_ascii=False))
            return body
        if name == "pae.session.open":
            return mem_mod.session_open(conn, params.get("actor") or "companion")
        if name == "pae.session.turn":
            ok, body = mem_mod.session_turn(conn, params.get("session_id"), params.get("role"),
                                            params.get("text"), params.get("refs"))
            if not ok:
                raise ValueError(json.dumps(body, ensure_ascii=False))
            return body
        return mem_mod.session_close(conn, params.get("session_id"), params.get("summary"))
    if name == "pae.propose":
        from . import params as params_mod
        from . import push as push_mod
        eng = get_engine()
        ok, body = params_mod.propose(eng.conn, params.get("actor") or actor,
                                      params.get("patch"), params.get("rationale", ""),
                                      params.get("evidence"), params.get("ttl_days"))
        if not ok:
            raise ValueError(json.dumps(body, ensure_ascii=False))
        eng.refresh_params()
        push_mod.notify(eng.conn, "param_changed",
                        {"decision_id": body.get("decision_id"), "after": body.get("applied_keys")},
                        actor=params.get("actor") or "companion")
        return body
    if name == "pae.decisions":
        from . import params as params_mod
        return params_mod.list_decisions(get_engine().conn, params.get("actor"),
                                         int(params.get("n") or 20))
    if name == "pae.pin":
        import time as _t
        eng = get_engine()
        actor_pin = params.get("actor") or actor
        # 配额：防批量「标记认识了」把注解静默关掉（AI 每天 ≤20）
        day_start = int(_t.time()) // 86400 * 86400
        used = eng.conn.execute(
            "SELECT COUNT(*) FROM events WHERE type='user_pin' AND source=? AND ts>=?",
            (actor_pin, day_start)).fetchone()[0]
        limit = 20
        if actor_pin not in ("user", "local") and used >= limit:
            raise ValueError(json.dumps(
                {"error": "pin_quota_exceeded", "actor": actor_pin, "used_today": used, "limit": limit,
                 "hint": "批量标记会静默关掉注解；如需更多请用户操作"}, ensure_ascii=False))
        return eng.record_event("user_pin", params.get("lemma") or "",
                               metas={"page_id": params.get("page_id") or "",
                                      "session_id": params.get("session_id") or ""},
                               source=actor_pin)
    if name == "pae.say":
        from . import push as push_mod
        ok, body = push_mod.say(get_engine().conn, params.get("actor") or actor,
                                params.get("text"), params.get("anchor") or "")
        if not ok:
            raise ValueError(json.dumps(body, ensure_ascii=False))
        return body
    if name == "pae.hint":
        from . import push as push_mod
        ok, body = push_mod.hint(get_engine().conn, params.get("actor") or "companion",
                                 params.get("lemma") or "", params.get("level") or "up")
        if not ok:
            raise ValueError(json.dumps(body, ensure_ascii=False))
        return body
    if name == "pae.agent.schedule":
        from . import scheduler as sched_mod
        return sched_mod.status(get_engine().conn)
    if name == "pae.agent.schedule.set":
        from . import scheduler as sched_mod
        cfg = sched_mod.config()
        want = params.get("enabled")
        if actor not in ("user", "local") and want:
            # 自我扩权防护的延伸：AI 不能给自己开调度
            raise ValueError(json.dumps({"error": "self_binding",
                                         "detail": "AI 不能为自己开启主动巡检（需用户操作）"},
                                        ensure_ascii=False))
        cfg["enabled"] = bool(want)
        if params.get("interval_min") is not None:
            cfg["interval_min"] = max(1, int(params["interval_min"]))
        if params.get("min_new_events") is not None:
            cfg["min_new_events"] = max(1, int(params["min_new_events"]))
        cfg.pop("_note", None)
        import json as _json
        _p = sched_mod.CONFIG   # 与调度读取同一个文件（可用 PAE_SCHEDULE_CONFIG 隔离）
        all_cfg = _json.loads(_p.read_text(encoding="utf-8"))
        all_cfg["agent_schedule"] = cfg
        _p.write_text(_json.dumps(all_cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if cfg.get("enabled"):
            sched_mod.start(get_engine().db_path)
        else:
            sched_mod.stop()
        return {"ok": True, "config": cfg}
    if name == "pae.agent.tick":
        from . import agent as agent_mod
        return agent_mod.tick(get_engine().conn, params.get("actor") or "companion",
                              params.get("stimulus") or "")
    if name == "pae.agent.log":
        from . import agent as agent_mod
        return agent_mod.log(get_engine().conn, int(params.get("n") or 20))
    if name == "pae.key.issue":
        if actor not in ("user", "local"):
            raise ValueError(json.dumps({"error": "forbidden",
                                         "detail": "只有用户能签发外部 key"}, ensure_ascii=False))
        who = params.get("name") or "ext:unnamed"
        plain = keys_mod.issue(who)
        return {"actor": who, "key": plain,
                "note": "明文只显示这一次；外部智能体用它作 X-PAE-Key"}
    if name == "pae.permissions":
        from . import agent as agent_mod
        from . import params as params_mod
        return {
            "principle": [
                "AI 能收紧自己，不能放宽自己（自我约束项只能调低）",
                "AI 能影响系统，不能覆盖用户（用户的事实/人格身份受保护）",
                "AI 的改动一律临时（必须带 ttl）；永久化由用户决定",
                "高影响变量：步长 ≤ 区间 20%、全局每天 ≤1 次",
                "AI 循环每次都是真实 LLM 花费，故限流",
            ],
            "actors": {"local": "本机用户（面板/扩展），最高写权，不受限",
                       "companion": "内置角色（进程内，走同一套门/配额/审计）",
                       "analyst": "分析角色（同上）",
                       "ext:<name>": "外部智能体（需 X-PAE-Key）"},
            "observe": "全部只读能力对任何 actor 开放（它要看得见才能判断）",
            "act_limits": {
                "propose.normal_knobs": {"ttl": "AI 必填", "cooldown": "同键 1 天"},
                "propose.high_impact": {"keys": list(params_mod.HIGH_IMPACT),
                                        "step_max_frac": params_mod.HIGH_IMPACT_MAX_STEP_FRAC,
                                        "daily_limit": params_mod.HIGH_IMPACT_DAILY_LIMIT,
                                        "ttl": "必填"},
                "propose.self_binding": {"keys": list(params_mod.SELF_BINDING),
                                         "rule": "AI 只能调低，调高需用户"},
                "persona.update": {"ai_may": "只改 5 个情绪轴，单轴 ±%.2f，同轴 7 天冷却"
                                             % params_mod.PERSONA_TRAIT_MAX_STEP,
                                   "ai_may_not": ["name", "address_user", "backstory_seed",
                                                  "style_notes", "quirks", "forbidden"],
                                   "drift_max": params_mod.PERSONA_DRIFT_MAX},
                "pin": {"ai_daily_limit": 20, "why": "批量标记=静默关掉注解"},
                "remember": {"evidence": "必填", "active_limit": 8,
                             "eviction": "优先淘汰 AI 自己写的，用户的事实不动"},
                "forget": {"ai_may_not": "抹掉用户写的事实"},
                "say": {"daily_cap": "取 daily_say_cap（AI 只能调低）"},
                "agent.tick": {"min_interval_s": agent_mod.TICK_MIN_INTERVAL_S,
                               "daily_limit": agent_mod.TICK_DAILY_LIMIT,
                               "why": "每次 tick 都是一次真实 LLM 调用"},
            },
        }
    if name == "pae.params":
        from . import params as params_mod
        conn = get_engine().conn
        eff = params_mod.effective(conn)
        return {
            "overrides": params_mod.current_overrides(conn),
            "effective": {k: eff.get(k) for k in params_mod.TUNABLE},
            "defaults": {k: params_mod.DEFAULTS_ALL.get(k) for k in params_mod.TUNABLE},
            "ranges": {k: list(v) for k, v in params_mod.RANGE_ALL.items()},
        }
    if name == "pae.push.log":
        from . import push as push_mod
        return push_mod.log(get_engine().conn, n=int(params.get("n") or 20), kind=params.get("kind"))
    if name == "pae.mute":
        # 静音闭环（2026-10-03 用户反馈修复）：扩展「静音」按钮此前只活在主世界内存里，
        # 引擎侧的主动说话（maybe_proactive）不知道，静音后仍会推送。现在读写打通。
        from . import push as push_mod
        eng = get_engine()
        if params.get("muted") is not None:
            push_mod.set_muted(eng.conn, bool(params.get("muted")))
        return {"muted": push_mod.is_muted(eng.conn)}
    if name == "pae.sensejudge":
        from . import engine as eng_mod
        from . import sensejudge as sj_mod
        eng = get_engine()
        if params.get("stats_only") or not params.get("lemma") or not params.get("sentence"):
            return sj_mod.stats(eng.conn)
        return sj_mod.judge(eng.conn, eng_mod.DICT_SQLITE_PATH, params.get("lemma"),
                            params.get("sentence"))
    if name == "pae.familiarity":
        from . import familiarity as fam_mod
        eng = get_engine()
        if params.get("recompute", True):
            fam_mod.recompute(eng.conn, eng.params, eng.states)
        mode = _fam_mode()
        rep = fam_mod.shadow_report(eng.conn, int(params.get("limit") or 20))
        rep["mode"] = mode
        rep["enforce_active"] = (mode == "enforce")
        return rep
    if name == "pae.familiarity.restore":
        from . import familiarity as fam_mod
        eng = get_engine()
        out = fam_mod.restore(eng.conn, params.get("lemma") or "", params.get("actor") or actor)
        fam_mod.recompute(eng.conn, eng.params, eng.states)
        return out
    if name == "pae.outcomes":
        from . import metrics as metrics_mod
        eng = get_engine()
        out = metrics_mod.compute_outcomes(eng.conn, int(params.get("eval_days") or 7))
        if params.get("evaluate_decisions", True):
            out["decision_effects"] = metrics_mod.evaluate_decisions(eng.conn)
        return out
    if name == "pae.trend":
        from . import observe as obs_mod
        return obs_mod.trend(get_engine(), int(params.get("days") or 7))
    if name == "pae.grammar":
        return {"point_id": params.get("point_id") or "", "state": "not_tracked",
                "note": "语法层未实现（23 号 ⑦ 设计稿；25 号种子资源已备）——这里如实返回，不装"}
    if name == "pae.persona":
        p = persona_mod.load_params()
        return {"params": p, "bands": persona_mod.bands(p), "prompt": persona_mod.render(p)}
    if name == "pae.persona.preview":
        p = params.get("params") or persona_mod.load_params()
        return {"prompt": persona_mod.render(p), "bands": persona_mod.bands(p),
                "issues": persona_mod.validate(p)}
    if name == "pae.persona.update":
        from . import params as params_mod
        newp = params.get("params") or {}
        ok, issues, axes = params_mod.persona_guard(get_engine().conn, params.get("actor") or actor, newp)
        if not ok:
            raise ValueError(json.dumps({"error": "persona_guard", "issues": issues}, ensure_ascii=False))
        ok2, issues2, path = persona_mod.save_params(newp)
        if not ok2:
            raise ValueError(json.dumps({"error": "invalid", "issues": issues2}, ensure_ascii=False))
        # 记进 decisions（可审计、可回滚，也是「同轴 7 天冷却」的依据）
        conn = get_engine().conn
        conn.execute(
            "INSERT INTO decisions(decision_id, ts, actor, capability, patch, rationale, evidence,"
            " before, after, applied, gate_result) VALUES(?,?,?,?,?,?,?,?,?,1,?)",
            ("dec_" + __import__("uuid").uuid4().hex[:16], int(time.time()),
             params.get("actor") or actor, "pae.persona.update",
             json.dumps([{"axis": k, "to": v["to"]} for k, v in axes.items()], ensure_ascii=False),
             params.get("rationale") or "", "[]", json.dumps(axes, ensure_ascii=False),
             json.dumps(axes, ensure_ascii=False), "persona_guard: ok"))
        conn.commit()
        return {"ok": True, "saved_to": path, "axes_changed": axes}
    if name == "pae.persona.say":
        # H2 配额（评审 2026-10-03）：persona.say 是真实 LLM 调用且此前**无任何日限**
        # （有日限的是推送型 pae.say）。每轮聊天一次调用，正常重度使用一天难超 120；
        # 恶意/失控循环（页面伪造桥消息、脚本死循环）会被这里掐断。本地 local 通道
        # （用户自己的机器）同样受限——防线防的是失控，不是防用户。
        day0 = int(time.time()) // 86400 * 86400
        used_say = int(get_engine().conn.execute(
            "SELECT COUNT(*) FROM cap_calls WHERE capability='pae.persona.say' AND ts>=? AND ok=1",
            (day0,)).fetchone()[0])
        if used_say >= 120:
            raise ValueError(json.dumps(
                {"error": "persona_say_daily_limit", "used_today": used_say, "limit": 120,
                 "hint": "每轮聊天都是真实 LLM 花费；明天自动重置"}, ensure_ascii=False))
        p = params.get("params") or persona_mod.load_params()
        cb = (params.get("context_block") or "").strip()
        c = None
        if not cb:
            # 自动装配：记忆（事实+学习状态）。这样「它记得你」才是真的
            try:
                from . import observe as obs_mod
                c = obs_mod.assemble(get_engine(), {"include": ["learner", "words", "metrics", "memory"],
                                                    "top_n": 6})
                lines = []
                for f in ((c.get("memory") or {}).get("facts") or [])[:8]:
                    lines.append("- 我记得：" + str(f.get("text")))
                # 注意（2026-10-02 首日反馈修复）：recent_turns **不再**拼进 context_block——
                # 拼在背景资料里模型不会当成「刚才的对话」，用户实测问「我第一句说了什么」它答非所问。
                # 它改走下面真正的 messages 历史（role=user/assistant）；facts/带/指标部分保留。
                band = (c.get("learner") or {}).get("band") or {}
                lines.append("- 学习带：带内 %s 词，带下 %s 词" % (band.get("in_band"), band.get("below_band")))
                m = c.get("metrics") or {}
                lines.append("- 近7日：遭遇 %s 次，悬停率 %s" % (m.get("encounters_7d"), m.get("hover_rate")))
                words = [w.get("lemma") for w in (c.get("words") or [])[:6]]
                if words:
                    lines.append("- 他最近在啃：" + "、".join([str(w) for w in words]))
                # 护栏（2026-10-02 就绪度判定）：样本不足时不许引用趋势数字——
                # 否则「这个词你总错」这类话会说得很像臆断，撞「做作」红线
                cohort = 0
                try:
                    from . import metrics as _m
                    cohort = int((_m.compute_outcomes(get_engine().conn).get("cohort_size") or 0))
                except Exception:
                    cohort = 0
                if cohort < 5:
                    lines.append("- ⚠️ 统计样本不足（成熟词仅 %d 个）：**不要引用任何趋势数字或" % cohort
                                 + "「你总是…」这类判断**，只说当下这个词本身")
                cb = "\n".join(lines)
            except Exception:
                cb = ""
        sysp = persona_mod.render(p, context_block=cb)
        # 长期记忆协议（2026-10-03 二次反馈）：此前整条链路没有任何生产者写 facts——
        # 用户在聊天里说「记住这个」，模型只会口头答应，侧栏「它记下的」其实一直拿
        # 最近 4 条对话凑数（旧 SW 兜底），既不长存也进不了她的上下文。现在给模型一个
        # 内联标记：用户让记时，回复末尾带 [[记住]] 行，引擎截走 → 真写 facts →
        # 下一次 say 自动注入（上面的「我记得」行）→ 侧栏印章卡显示真事实。
        sysp += ("\n\n（长期记忆协议）用户明确让你「记住」某件事时，在你回复的最末尾另起一行写：\n"
                 "[[记住]] 一句客观转述（60字以内）。\n"
                 "系统会截走这一行存进长期记忆（侧栏「它记下的」可见，之后每次对话你都会想起它），"
                 "这行不会显示给用户。用户没有让记的时候，绝对不要写这一行。")
        # 长上下文（2026-10-03 二次反馈）：6 轮窗口太短——用户实测聊约 30 轮后，
        # 她连用户第一句说了什么都答不上来。模型上下文 128K，历史按
        # config chat_history_turns（默认 64 轮 ≈ 32 次问答）整段作为消息数组送入。
        hist_n = 64
        try:
            hist_n = int(_config().get("chat_history_turns") or 64)
        except Exception:
            hist_n = 64
        hist_n = max(8, min(200, hist_n))
        try:
            from . import memory as mem_mod
            turns = mem_mod.recent(get_engine().conn, None, hist_n)
        except Exception:
            turns = []
        history = []
        for t in (turns or [])[-hist_n:]:
            role = str((t or {}).get("role") or "").strip().lower()
            role = "assistant" if role in ("agent", "assistant") else ("user" if role == "user" else "")
            text = str((t or {}).get("text") or "").strip()
            if role and text:
                history.append({"role": role, "content": text})
        stimulus = params.get("stimulus") or "（没有输入）"
        try:
            temp = float(params.get("temperature", 0.7))
        except Exception:
            temp = 0.7
        msgs = [{"role": "system", "content": sysp}] + history + [{"role": "user", "content": stimulus}]
        t0 = time.time()
        r = llm_mod.chat(msgs, temperature=temp, timeout=90)
        reply = (r.get("content") or "").strip()
        # [[记住]] 协议落库：截走标记行 → 真写 facts（带当日限流，防模型自嗨刷库）
        remembered = None
        mark_lines = [ln for ln in reply.splitlines() if ("[[记住]]" in ln) or ("【记住】" in ln)]
        if mark_lines:
            import re as _re
            fact_txt = ""
            for ln in mark_lines:
                cand = _re.sub(r"\[\[记住\]\]|【记住】", "", ln).strip().strip("「」‘’“”\"' ")
                if cand:
                    fact_txt = cand
                    break
            if fact_txt:
                day0 = int(time.time()) // 86400 * 86400
                used_today = int(get_engine().conn.execute(
                    "SELECT COUNT(*) FROM facts WHERE source_actor='companion' AND created_at >= ?",
                    (day0,)).fetchone()[0])
                if used_today >= 8:
                    remembered = {"ok": False, "skipped": "daily_fact_limit", "used_today": used_today}
                else:
                    from . import memory as _mem
                    okf, fbody = _mem.remember(get_engine().conn, "companion", "event",
                                               fact_txt[:200], [{"stimulus": stimulus[:300]}],
                                               confidence=0.8)
                    remembered = {"ok": bool(okf), "via": "marker"}
                    if isinstance(fbody, dict):
                        remembered.update(fbody)
            reply = "\n".join(ln for ln in reply.splitlines()
                              if ("[[记住]]" not in ln) and ("【记住】" not in ln)).strip()
        # 意图兜底（2026-10-03 三次反馈）：模型没写标记行，但用户话里明确让记 →
        # 直接把用户原话存为事实。确定性 regex，不依赖模型配合率。
        if not mark_lines:
            intent_txt = _extract_remember_intent(stimulus)
            if intent_txt:
                day0 = int(time.time()) // 86400 * 86400
                used_today = int(get_engine().conn.execute(
                    "SELECT COUNT(*) FROM facts WHERE source_actor='user' AND created_at >= ?",
                    (day0,)).fetchone()[0])
                if used_today >= 30:
                    remembered = {"ok": False, "skipped": "daily_intent_limit", "used_today": used_today}
                else:
                    from . import memory as _mem
                    okf, fbody = _mem.remember(get_engine().conn, "user", "preference",
                                               intent_txt[:200],
                                               [{"via": "chat_intent", "stimulus": stimulus[:300]}],
                                               confidence=0.9)
                    remembered = {"ok": bool(okf), "via": "intent"}
                    if isinstance(fbody, dict):
                        remembered.update(fbody)
        if not reply:
            reply = "记下了。"
        # 说出的话必须落库，否则「下一轮记得上一轮」没有数据源（见 _remember_turns 的说明）
        try:
            _remember_turns(get_engine().conn, stimulus, reply)
        except Exception:
            pass
        return {"reply": reply, "elapsed_ms": int((time.time() - t0) * 1000),
                "model": r.get("model"), "usage": r.get("usage"),
                "history_turns": len(history), "remembered": remembered}
    return None


def handle(name: str, params: dict, key: str = "", actor_hint: str = None):
    """统一入口。返回 (http_status, body)。

    actor_hint：**仅**给进程内置角色（companion/analyst）用。它省掉的只是「key→actor 解析」这一步，
    后面的 scope 检查、守门、配额、审计**一个都不少**——这是有意的接缝，写在 36 号文档里。
    """
    if actor_hint:
        actor, scopes = actor_hint, ["read", "write"]
    else:
        actor, scopes = keys_mod.resolve(key)
    # 安全：**身份只能来自认证**。params 里的 actor 仅在「无 key 的本机通道」生效
    # （那是用户自己的机器，用它模拟某个 actor 做测试/触发是合理的）；
    # 一旦带了 key（外部智能体），params 里的 actor 一律忽略，否则可以自称 user 提权。
    if actor == "local" and isinstance(params, dict) and params.get("actor"):
        actor = str(params["actor"])
    if actor is None:
        audit("unknown", name, False, "unauthorized")  # 被拒也要留痕：能看见谁试过什么
        return 401, {"ok": False, "error": "unauthorized", "detail": "unknown X-PAE-Key"}
    cap = find(name)
    if cap is None:
        audit(actor, name, False, "unknown_capability")
        return 404, {"ok": False, "error": "unknown_capability", "name": name,
                     "hint": "GET /v1/cap/manifest"}
    if cap.get("status") == "planned":
        audit(actor, name, False, "not_implemented")
        return 501, {"ok": False, "error": "not_implemented", "name": name,
                     "status": cap.get("status"),
                     "note": "能力已在清单中登记，但实现未完成——见 /v1/cap/manifest 的 summary"}
    need = cap.get("scope") or "read"
    if need not in (scopes or []):
        audit(actor, name, False, "scope_denied")
        return 403, {"ok": False, "error": "scope_denied", "need": need, "have": scopes}
    try:
        result = _dispatch(name, params or {}, actor)
    except Exception as e:
        audit(actor, name, False, "%s: %s" % (type(e).__name__, e))
        return 500, {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200])}
    audit(actor, name, True, "")
    return 200, {"ok": True, "actor": actor, "capability": name, "result": result}
