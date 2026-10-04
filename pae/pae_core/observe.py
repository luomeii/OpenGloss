# -*- coding: utf-8 -*-
"""PAE 只读能力：context 装配 / page 现场 / word / events / decisions。

上下文策略（用户 2026-10-01 拍板）：1M 上下文很充裕 → **默认全量装载**，
只在超过单请求上限时按固定顺序渐进裁剪（先丢最老的对话 → 再丢低置信记忆 → 最后丢低 S 的词）。
"""
import json
import time
from pathlib import Path

from . import metrics as metrics_mod
from . import persona as persona_mod

ROOT = Path(__file__).resolve().parents[1]


def _config() -> dict:
    try:
        return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def estimate_tokens(obj) -> int:
    """粗估 token：中文密集时约 1 token/字，取 1/3 保守估计。"""
    try:
        return max(1, len(json.dumps(obj, ensure_ascii=False)) // 3)
    except Exception:
        return 1


def band_words(engine, n=40, order="recent"):
    """学习带内（含带下新词）的词，附释义。

    order='recent'（默认；2026-10-02 首日反馈修复）：按最后接触时间 last_seen 降序——
    侧栏「最近在啃」就该显示最近真碰到的词。旧实现一律按 S 升序，低 S 的旧词永远霸榜，
    新接触的词挤不进前 n（用户实测：翻了很多页，那几个词还是不变）。
    order='s'：按 S 升序（最该被关注的在前），保留给「最该关注」语义的旧调用方。
    同一时间戳（或都没有 last_seen）时用 S 升序兜底，保证顺序稳定、可复现。
    """
    lo = engine.params.get("band_lo", 0.10)
    hi = engine.params.get("band_hi", 0.55)
    items = []
    for (lemma, sense), st in engine.states.items():
        s = st.s_value(engine.params)
        if s > hi:
            continue  # 已出带，不再占用预算
        entry = engine.translations.get(lemma)
        items.append({
            "lemma": lemma,
            "gloss": (entry[0] if entry else "")[:60],
            "s_value": round(s, 3),
            "below_band": s < lo,
            "encounters": st.n_enc,
            "hover": st.n_hover,
            "known_click": st.n_known,
            "last_seen": st.t_E,
        })
    if order == "s":
        items.sort(key=lambda x: x["s_value"])
    else:
        items.sort(key=lambda x: (-(x["last_seen"] or 0), x["s_value"]))
    return items[:n]


def word(engine, lemma: str):
    lemma = (lemma or "").strip().lower()
    if not lemma:
        return {"error": "empty lemma"}
    st = engine.states.get((lemma, 0))
    entry = engine.translations.get(lemma)
    s = st.s_value(engine.params) if st else 0.0
    lo = engine.params.get("band_lo", 0.10)
    hi = engine.params.get("band_hi", 0.55)
    return {
        "lemma": lemma,
        "gloss": entry[0] if entry else "",
        "in_dict": bool(entry),
        "s_value": round(s, 3),
        "band": "below" if s < lo else ("in" if s <= hi else "above"),
        "encounters": st.n_enc if st else 0,
        "hover": st.n_hover if st else 0,
        "known_click": st.n_known if st else 0,
        "last_seen": st.t_E if st else None,
    }


def events(engine, params: dict):
    days = int(params.get("days") or 7)
    since = int(time.time()) - days * 86400
    sql = "SELECT ts, type, payload FROM events WHERE ts >= ?"
    args = [since]
    if params.get("type"):
        sql += " AND type = ?"
        args.append(params["type"])
    if params.get("lemma"):
        sql += " AND json_extract(payload, '$.lemma') = ?"
        args.append(params["lemma"])
    sql += " ORDER BY ts DESC LIMIT ?"
    args.append(int(params.get("n") or 50))
    out = []
    for ts, etype, payload in engine.conn.execute(sql, args):
        try:
            p = json.loads(payload or "{}")
        except Exception:
            p = {}
        out.append({"ts": ts, "type": etype, "payload": p})
    return out


def decisions(engine, params: dict):
    """决策/调用审计：哪个 actor 动了哪项能力。"""
    n = int(params.get("n") or 20)
    sql = "SELECT ts, actor, capability, ok, detail FROM cap_calls"
    args = []
    if params.get("actor"):
        sql += " WHERE actor = ?"
        args.append(params["actor"])
    sql += " ORDER BY call_id DESC LIMIT ?"
    args.append(n)
    return [{"ts": t, "actor": a, "capability": c, "ok": bool(o), "detail": d}
            for t, a, c, o, d in engine.conn.execute(sql, args)]


def trend(engine, days=7):
    """按天的遭遇/新词/曝光曲线（趋势可读）。"""
    since = int(time.time()) - days * 86400
    buckets = {}
    for ts, etype, payload in engine.conn.execute(
            "SELECT ts, type, payload FROM events WHERE ts >= ?", (since,)):
        # 与 push/scheduler/sensejudge/capsurface 一致，统一用 UTC 日；
        # 之前这里用 localtime，UTC+8 下趋势与每日配额会差 8 小时。
        day = time.strftime("%Y-%m-%d", time.gmtime(ts))
        b = buckets.setdefault(day, {"date": day, "encounter": 0, "annotation_shown": 0,
                                     "hover": 0, "known_click": 0, "new_lemmas": set()})
        if etype in b:
            b[etype] += 1
        if etype == "encounter":
            try:
                p = json.loads(payload or "{}")
                if p.get("lemma"):
                    b["new_lemmas"].add(p["lemma"])
            except Exception:
                pass
    series = []
    for day in sorted(buckets):
        b = buckets[day]
        b["distinct_lemmas"] = len(b.pop("new_lemmas"))
        series.append(b)
    return {"days": days, "series": series}


def page(engine, params: dict):
    """页面现场：优先用调用方给的；否则取最近一次曝光上报的页面。"""
    if params.get("url") or params.get("annotated"):
        return {
            "url": params.get("url") or "",
            "annotated": params.get("annotated") or [],
            "selection": params.get("selection") or "",
            "source": "caller",
        }
    row = engine.conn.execute(
        "SELECT ts, payload FROM events WHERE type='annotation_shown' ORDER BY ts DESC LIMIT 1"
    ).fetchone()
    if not row:
        return {"url": "", "annotated": [], "selection": "", "source": "none",
                "note": "还没有页面曝光记录（扩展未上报或尚未阅读）"}
    ts, payload = row
    try:
        p = json.loads(payload or "{}")
    except Exception:
        p = {}
    return {
        "url": p.get("page_id") or p.get("url") or "",
        "annotated": [p.get("lemma")] if p.get("lemma") else [],
        "selection": "",
        "source": "last_exposure",
        "ts": ts,
    }


def assemble(engine, params: dict):
    """L0–L6 完整装配（1M 策略：默认全量）。"""
    cfg = _config()
    include = params.get("include") or ["persona", "learner", "words", "metrics", "page", "limits", "self", "memory"]
    top_n = int(params.get("top_n") or 40)
    days = int(params.get("days") or 7)
    ctx = {}

    if "persona" in include:
        pp = persona_mod.load_params()
        ctx["persona"] = {"params": pp, "prompt": persona_mod.render(pp)}
    if "learner" in include:
        total = engine.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        ctx["learner"] = {
            "total_events": total,
            "lexicon_size": len(engine.translations),
            "tracked_lemmas": len(engine.states),
            "band": metrics_mod.band_population(engine.states, engine.params),
        }
    if "words" in include:
        ctx["words"] = band_words(engine, top_n)
    if "metrics" in include:
        ctx["metrics"] = metrics_mod.compute(engine.conn, days=days)
        try:
            ctx["outcomes"] = metrics_mod.compute_outcomes(engine.conn)
        except Exception:
            ctx["outcomes"] = None
    if "page" in include:
        ctx["page"] = page(engine, params.get("page") or {})
    if "self" in include:
        # 「自己」：最近的思考、今天说过几句、已经改过哪些变量——没有这块，它会重复啰嗦且不知额度
        from . import params as params_mod
        from . import push as push_mod
        try:
            from . import agent as agent_mod
            recent = agent_mod.log(engine.conn, 5)
        except Exception:
            recent = []
        try:
            from . import push as push_mod2
            said = push_mod2.today_count(engine.conn, "say")
        except Exception:
            said = 0
        ctx["self"] = {
            "recent_thoughts": [{"ts": r["ts"], "thought": r["thought"],
                                 "actions": [a.get("name") for a in (r.get("result") or {}).get("results", [])]}
                                for r in recent],
            "said_today": said,
            "current_overrides": params_mod.current_overrides(engine.conn),
        }
    if "memory" in include:
        # 关键接线：把**记住的东西**装回上下文——否则记忆只是档案，不是回忆
        from . import memory as mem_mod
        try:
            facts = mem_mod.active_facts(engine.conn, 10)
        except Exception:
            facts = []
        try:
            turns = mem_mod.recent(engine.conn, None, 6)
        except Exception:
            turns = []
        ctx["memory"] = {
            "facts": [{"kind": f.get("kind"), "text": f.get("text"),
                       "confidence": f.get("confidence"), "evidence": f.get("evidence")} for f in facts],
            "recent_turns": [{"role": t.get("role"), "text": t.get("text"), "ts": t.get("ts")} for t in turns],
            "note": "facts 是长期锚点；recent_turns 是最近对话。开口前先看这两块。",
        }
    if "limits" in include:
        from . import params as params_mod
        eff = params_mod.effective(engine.conn)   # ← 读**有效参数**，不再读静态 config（修自我感知漂移）
        ctx["limits"] = {
            "annotate_max_s": eff.get("annotate_max_s"),
            "budget_per_request": int(cfg.get("budget", 8)),
            "daily_say_cap": eff.get("daily_say_cap"),
            "max_annotations_per_page": eff.get("max_ann_per_page"),
            "source": "effective_params",
        }

    cap = int(params.get("max_tokens") or cfg.get("context_max_tokens", 200000))
    toks = estimate_tokens(ctx)
    trimmed = []
    # 渐进裁剪顺序：低 S 的词 → metrics 明细 → 页面
    while toks > cap and ctx.get("words"):
        half = max(1, len(ctx["words"]) // 2)
        ctx["words"] = ctx["words"][:half]
        trimmed.append("words:" + str(half))
        toks = estimate_tokens(ctx)
    if toks > cap and ctx.get("metrics"):
        ctx["metrics"].pop("top_ignored_words", None)
        trimmed.append("metrics.top_ignored_words")
        toks = estimate_tokens(ctx)
    ctx["budget_tokens"] = {"estimate": toks, "cap": cap, "trimmed": trimmed, "policy": "1M_full_then_trim"}
    return ctx
