# -*- coding: utf-8 -*-
"""PAE 指标：从事件流算出的「引擎表现」。反馈闭环的输入。

设计：不新增存储，一切从 events 推导（事件溯源的红利）。
"""
import json
import time


def _payload(row):
    try:
        return json.loads(row[2] or "{}")
    except Exception:
        return {}


def _rows(conn, since):
    return conn.execute(
        "SELECT ts, type, payload FROM events WHERE ts >= ? ORDER BY ts ASC", (since,)
    ).fetchall()


def compute(conn, days=7, min_exposure=3, top_n=10):
    """返回指标字典。days=统计窗口天数。"""
    now = int(time.time())
    since = now - days * 86400
    rows = _rows(conn, since)

    enc = 0
    exposure = {}   # lemma -> 曝光数
    hover = {}      # lemma -> 悬停数
    click = {}      # lemma -> 「认识了」数
    per_page = {}   # (page_id) -> 曝光数（用于预算利用率）
    lemmas_seen = set()
    for ts, etype, payload in rows:
        p = _payload((ts, etype, payload))
        lemma = p.get("lemma") or p.get("lemma_id") or ""
        if etype == "encounter":
            enc += 1
            if lemma:
                lemmas_seen.add(lemma)
        elif etype == "annotation_shown":
            if lemma:
                exposure[lemma] = exposure.get(lemma, 0) + 1
                pg = p.get("page_id") or ""
                per_page[pg] = per_page.get(pg, 0) + 1
        elif etype == "hover":
            if lemma:
                hover[lemma] = hover.get(lemma, 0) + 1
        elif etype == "known_click":
            if lemma:
                click[lemma] = click.get(lemma, 0) + 1

    # 新词：首次遭遇落在窗口内
    first_seen = {}
    for ts, lemma in conn.execute(
            "SELECT MIN(ts), json_extract(payload, '$.lemma') FROM events"
            " WHERE type='encounter' GROUP BY json_extract(payload, '$.lemma')"):
        if lemma:
            first_seen[lemma] = ts
    new_lemmas = [w for w, ts in first_seen.items() if ts and ts >= since]

    total_exposure = sum(exposure.values())
    total_hover = sum(hover.values())
    total_click = sum(click.values())
    exposure = {k: v for k, v in exposure.items() if v >= min_exposure}
    ignored = []
    for lemma, n in exposure.items():
        h = hover.get(lemma, 0)
        c = click.get(lemma, 0)
        ignored.append({
            "lemma": lemma,
            "exposure": n,
            "hover": h,
            "click": c,
            "ignore_rate": round(1.0 - (h + c) / n, 3),
        })
    ignored.sort(key=lambda x: (-x["ignore_rate"], -x["exposure"]))

    budget = 8  # 每请求预算（26 号算法规格）
    pages = [v for v in per_page.values() if v > 0]
    avg_per_page = (sum(pages) / len(pages)) if pages else 0.0
    return {
        "window_days": days,
        "generated_at": now,
        "encounters_7d": enc,
        "lemmas_touched_7d": len(lemmas_seen),
        "new_lemmas_7d": len(new_lemmas),
        "annotation_exposure": total_exposure,
        "hover_rate": round(total_hover / total_exposure, 3) if total_exposure else 0.0,
        "click_rate": round(total_click / total_exposure, 3) if total_exposure else 0.0,
        "budget_utilization": round(min(1.0, avg_per_page / budget), 3) if pages else 0.0,
        "avg_annotations_per_page": round(avg_per_page, 2),
        "top_ignored_words": ignored[:top_n],
        "note": "top_ignored_words = 曝光多但几乎没人悬停/点击的词 → 这是「噪声词」的直接证据",
    }


def band_population(states, params):
    """学习带分布：带下(新词) / 带内 / 带上(熟词)。"""
    lo = params.get("band_lo", 0.10)
    hi = params.get("band_hi", 0.55)
    out = {"below_band": 0, "in_band": 0, "above_band": 0, "total": 0}
    for st in states.values():
        s = st.s_value(params)
        out["total"] += 1
        if s < lo:
            out["below_band"] += 1
        elif s <= hi:
            out["in_band"] += 1
        else:
            out["above_band"] += 1
    return out

# ---------------------------------------------------------------------------
# 结果侧指标（尺子）：投入侧指标回答「系统做了什么」，这一块回答「有没有用」。
# 来自独立评审：没有结果指标 → AI 调参没有优化目标，也无法被证伪地改进。
# ---------------------------------------------------------------------------

def _first_exposure(conn):
    """每个词第一次被注解展示的时间。"""
    out = {}
    for ts, payload in conn.execute("SELECT ts, payload FROM events WHERE type = 'annotation_shown'"):
        try:
            lemma = json.loads(payload or "{}").get("lemma")
        except Exception:
            lemma = None
        if not lemma:
            continue
        if lemma not in out or ts < out[lemma]:
            out[lemma] = ts
    return out


def _action_times(conn, etype):
    """每个词某类行为的时间列表。"""
    out = {}
    for ts, payload in conn.execute("SELECT ts, payload FROM events WHERE type = ?", (etype,)):
        try:
            lemma = json.loads(payload or "{}").get("lemma")
        except Exception:
            lemma = None
        if lemma:
            out.setdefault(lemma, []).append(ts)
    return out


def compute_outcomes(conn, eval_days=7, cohort_max_days=30, early_max_days=14, early_lag_days=3):
    """结果侧指标。

    cohort = 首次曝光发生在 [now-cohort_max_days, now-eval_days] 的词
             （即「至少 7 天前就已经见过」的词——够成熟，能判断有没有用）
    converted = 这些词里后来点过「认识了」的

    M15（**追加**，不动上面的主口径）：再加一组**早期读数**，解决「新用户前 7 天仪表盘全空」。
    early_cohort = 首次曝光在 [now-early_max_days, now-early_lag_days]（默认 [14 天前, 3 天前]）
                   **且尚未进入上面的成熟队列**的词——成熟队列里已经算过的词不重复计入。
    early_conversion_rate 与 conversion_rate **同口径**（队列里后来点过 known_click 的比例），
    只是窗口更短：样本小、波动大，只能当**先行趋势**读，替代不了北极星。
    """
    now = int(time.time())
    low = now - cohort_max_days * 86400
    high = now - eval_days * 86400
    first = _first_exposure(conn)
    known = _action_times(conn, "known_click")
    hover = _action_times(conn, "hover")

    cohort = {w: t for w, t in first.items() if low <= t <= high}
    converted, engaged, silent, days_to_known = [], [], [], []
    for w, t0 in cohort.items():
        ks = [t for t in known.get(w, []) if t >= t0]
        hs = [t for t in hover.get(w, []) if t >= t0]
        if ks:
            converted.append(w)
            days_to_known.append(min((min(ks) - t0) / 86400.0, 999))
        elif hs:
            engaged.append(w)
        else:
            silent.append(w)

    # --- M15：早期读数（新增，不动上面的成熟口径）---
    # 队列 = 首遇在 [now-early_max_days, now-early_lag_days] 且**晚于**成熟窗口上界 high 的词；
    # 口径与 conversion_rate 完全一致（只是窗口更短），并额外给一句说明。
    early_lo = now - early_max_days * 86400
    early_hi = now - early_lag_days * 86400
    early = {w: t for w, t in first.items() if early_lo <= t <= early_hi and t > high}
    early_converted = 0
    for w, t0 in early.items():
        if any(t >= t0 for t in known.get(w, [])):
            early_converted += 1
    early_n = len(early)

    # 决策有效率（设计评审指定的三数字之一）：已评估的决策里「有改善」的比例
    win_rate = None
    try:
        rows = list(conn.execute("SELECT effect_result FROM decisions WHERE effect_result IS NOT NULL"))
        good = sum(1 for (e,) in rows if e and "better" in str(e))
        bad = sum(1 for (e,) in rows if e and "worse" in str(e))
        win_rate = round(good / (good + bad), 3) if (good + bad) else None
    except Exception:
        pass

    # 打扰率（三数字之三）：每阅读小时它开口几次。红线的量化看门狗，升即停。
    interrupt = None
    try:
        pushes = conn.execute("SELECT COUNT(*) FROM push_log WHERE ts >= ?",
                              (now - 7 * 86400,)).fetchone()[0]
        buckets = conn.execute(
            "SELECT COUNT(DISTINCT COALESCE(json_extract(payload,'$.page_id'),'') || '|' || (ts/3600))"
            " FROM events WHERE type='annotation_shown' AND ts >= ?", (now - 7 * 86400,)).fetchone()[0]
        hours = (buckets or 0) / 7.0
        interrupt = round(pushes / hours, 3) if hours >= 1 else None
    except Exception:
        pass

    n = len(cohort)
    med = 0.0
    if days_to_known:
        s = sorted(days_to_known)
        med = round(s[len(s) // 2], 2)
    return {
        "eval_days": eval_days,
        "cohort_size": n,
        "converted": len(converted),
        "engaged_not_converted": len(engaged),
        "ignored": len(silent),
        "conversion_rate": round(len(converted) / n, 3) if n else None,
        "engagement_rate": round((len(converted) + len(engaged)) / n, 3) if n else None,
        "median_days_to_known": med,
        "early_cohort_size": early_n,
        "early_conversion_rate": round(early_converted / early_n, 3) if early_n else None,
        "early_note": "early_* = 早期口径（队列=首遇在 [now-%dd, now-%dd] 且尚未进入成熟队列的词，"
                      "实际即 %d-%d 天前首遇）；与 conversion_rate 同口径、只换窗口——样本小、波动大，"
                      "只作先行趋势读数，替代不了北极星。"
                      % (early_max_days, early_lag_days, early_lag_days, eval_days),
        "decision_win_rate": win_rate,
        "interrupt_rate": interrupt,
        "note": "conversion_rate = 至少 eval_days 天前就被注解过的词里，后来真的点过「认识了」的比例。"
                "这是本系统的**结果侧北极星**：它不涨，说明注解只是噪音。",
        "interrupt_note": "interrupt_rate = 近 7 天推送条数 ÷ 阅读小时数（页面×小时桶 ÷ 7 的近似）。"
    }


def evaluate_decisions(conn, window_days=7, limit=20):
    """给每次已生效的参数变更算「改前 vs 改后」的效果，并写回 decisions.effect_result。

    这把「ttl 到期就回滚」升级成**实验闭环**：好改动可以被看见并提升为永久；坏改动被识别。
    """
    import json as _json
    rows = list(conn.execute(
        "SELECT decision_id, ts, capability, patch, after FROM decisions"
        " WHERE applied = 1 AND (effect_result IS NULL OR effect_result = 'auto_reverted_ttl')"
        " ORDER BY ts DESC LIMIT ?", (int(limit),)))
    out = []
    for did, ts, capability, patch, after in rows:
        w = int(window_days) * 86400
        before = {"exposure": 0, "hover": 0, "click": 0}
        after_m = {"exposure": 0, "hover": 0, "click": 0}
        for t, etype in conn.execute(
                "SELECT ts, type FROM events WHERE ts >= ? AND ts < ?", (ts - w, ts)):
            if etype == "annotation_shown":
                before["exposure"] += 1
            elif etype == "hover":
                before["hover"] += 1
            elif etype == "known_click":
                before["click"] += 1
        for t, etype in conn.execute(
                "SELECT ts, type FROM events WHERE ts >= ? AND ts < ?", (ts, ts + w)):
            if etype == "annotation_shown":
                after_m["exposure"] += 1
            elif etype == "hover":
                after_m["hover"] += 1
            elif etype == "known_click":
                after_m["click"] += 1

        def rate(m):
            return round(m["hover"] / m["exposure"], 3) if m["exposure"] else None

        verdict = "insufficient_data"
        if before["exposure"] >= 5 and after_m["exposure"] >= 5:
            b, a = rate(before), rate(after_m)
            if b is None or a is None:
                verdict = "insufficient_data"
            elif a > b + 0.05:
                verdict = "better (hover_rate up)"
            elif a < b - 0.05:
                verdict = "worse (hover_rate down)"
            else:
                verdict = "neutral"
        result = {"before": before, "after": after_m, "hover_rate_before": rate(before),
                  "hover_rate_after": rate(after_m), "verdict": verdict,
                  "window_days": window_days, "evaluated_at": int(time.time())}
        conn.execute("UPDATE decisions SET effect_checked_at = ?, effect_result = ? WHERE decision_id = ?",
                     (int(time.time()), _json.dumps(result, ensure_ascii=False), did))
        out.append({"decision_id": did, "capability": capability, **result})
    conn.commit()
    return out