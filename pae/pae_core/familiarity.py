# -*- coding: utf-8 -*-
"""熟悉度投影（35 号规格实现）——「无视即熟悉」的影子已知集。

三条铁律（39 号认定）：
1. **纯派生**：只读已有事件，不改 S、不加事件类型、不碰冻结内核
2. **只影响选不选**：enforce 模式下才跳过；默认 shadow（只记录，行为零变化）
3. **可重建、可解释、可反悔**：删投影可重算；每条带 reason_code；一键恢复 + 冷却

合格曝光口径（当前为**降级口径**，见 39 号）：
  扩展上报 page_active_ms（页面活跃时长）≥ QUALIFIED_PAGE_MS 的曝光算合格。
  真正的「元素级视口停留」留待后续升级；此处如实标注 rule 版本与口径。
"""
import json
import time

RULE_VERSION = 2
QUALIFIED_PAGE_MS = 1000      # 降级口径：页面活跃 ≥1s 才算「看见了」

# ---- 2026-10-02 算法复审修正（独立重算确认的死锁）----
# 旧参数 R1=8 / R6=annotate_max_s(0.75) 构成循环死锁：
#   曝光喂 E → 第 6-7 次曝光时 S≈0.75 → 词因 S≥0.75 **自然停止注解** → 曝光不再增长
#   → R1 的「8 次曝光」永远达不到 → 豁免清单永远为空（机制空转）。
# 修正：R1 降到 5（在自然出带点 6-7 次之前留出余量），R6 上限与注解门槛**解耦**到 0.85。
R1_MIN_EXPOSURES = 5          # 合格曝光数（= 不同「页面×小时」桶的数量）
R2_MIN_DAYS = 3               # 曝光覆盖的不同本地日
R5_NEW_WORD_DAYS = 7          # 新词保护期
R6_S_CEILING = 0.85           # 豁免的 S 上限（刻意高于注解门槛 0.75，切断死锁）
R7_REVOKE_COOLDOWN_S = 30 * 86400
WINDOW_DAYS = 14
REASON_IGNORED = "IGNORED_5X_3D"
REASON_COOLDOWN = "MANUAL_REVOKE_COOLDOWN"

DDL = """
CREATE TABLE IF NOT EXISTS familiarity_override (
    lemma   TEXT PRIMARY KEY,
    action  TEXT NOT NULL,
    ts      INTEGER NOT NULL,
    source  TEXT
);
CREATE TABLE IF NOT EXISTS familiarity_projection (
    lemma          TEXT PRIMARY KEY,
    state          TEXT NOT NULL,
    q_exp          INTEGER NOT NULL,
    distinct_days  INTEGER NOT NULL,
    first_seen     INTEGER,
    last_hover_at  INTEGER,
    decided_at     INTEGER NOT NULL,
    reason_code    TEXT,
    rule_version   INTEGER NOT NULL
);
"""


def ensure_tables(conn):
    conn.executescript(DDL)
    conn.commit()


def _exposure_stats(conn, window_days=WINDOW_DAYS):
    """按词统计曝光/悬停/点击/首次出现（全部来自已有事件）。"""
    since = int(time.time()) - int(window_days) * 86400
    stats = {}
    for ts, etype, payload in conn.execute(
            "SELECT ts, type, payload FROM events WHERE type IN ('annotation_shown','hover','known_click')",):
        try:
            p = json.loads(payload or "{}")
        except Exception:
            continue
        lemma = p.get("lemma") or p.get("lemma_id")
        if not lemma:
            continue
        s = stats.setdefault(lemma, {"q_exp": 0, "days": set(), "hover": 0, "click": 0,
                                     "first_seen": None, "last_hover_at": None, "exp_buckets": set()})
        if etype == "annotation_shown":
            if s["first_seen"] is None or ts < s["first_seen"]:
                s["first_seen"] = ts
            if ts >= since:
                vms = p.get("page_active_ms")
                # 降级口径：没报 page_active_ms 的旧数据按「视为合格」处理（避免把历史数据全判为不合格）
                if vms is None or int(vms) >= QUALIFIED_PAGE_MS:
                    page = p.get("page_id") or ""
                    bucket = (page, ts // 3600)
                    if bucket not in s["exp_buckets"]:
                        s["exp_buckets"].add(bucket)
                        s["q_exp"] += 1
                        s["days"].add(ts // 86400)
        elif etype == "hover":
            if ts >= since:
                s["hover"] += 1
            if s["last_hover_at"] is None or ts > s["last_hover_at"]:
                s["last_hover_at"] = ts
        elif etype == "known_click":
            if ts >= since:
                s["click"] += 1
    return stats


def _overrides(conn):
    out = {}
    for lemma, action, ts, source in conn.execute(
            "SELECT lemma, action, ts, source FROM familiarity_override"):
        out[lemma] = {"action": action, "ts": ts, "source": source}
    return out


def evaluate(conn, engine_params, states, window_days=WINDOW_DAYS):
    """按 R1–R8 判定每个词是否「该被豁免」。返回 {lemma: {...}}（含未豁免的，便于审计）。"""
    stats = _exposure_stats(conn, window_days)
    ovr = _overrides(conn)
    now = int(time.time())
    out = {}
    for lemma, s in stats.items():
        st = states.get((lemma, 0))
        s_value = st.s_value(engine_params) if st is not None else 0.0
        reasons = []
        state = "none"
        o = ovr.get(lemma)
        if o and o["action"] == "revoke" and now - o["ts"] < R7_REVOKE_COOLDOWN_S:
            reasons.append(REASON_COOLDOWN)
        else:
            if s["q_exp"] < R1_MIN_EXPOSURES:
                reasons.append("R1 exp %d/%d" % (s["q_exp"], R1_MIN_EXPOSURES))
            if len(s["days"]) < R2_MIN_DAYS:
                reasons.append("R2 days %d/%d" % (len(s["days"]), R2_MIN_DAYS))
            if s["hover"] > 0:
                reasons.append("R3 hover=%d" % s["hover"])
            if s["click"] > 0:
                reasons.append("R4 click=%d" % s["click"])
            if s["first_seen"] and now - s["first_seen"] < R5_NEW_WORD_DAYS * 86400:
                reasons.append("R5 new")
            if s_value >= R6_S_CEILING:
                reasons.append("R6 s=%.2f>=%.2f" % (s_value, R6_S_CEILING))
            if not reasons:
                state = "exempt"
        out[lemma] = {"state": state, "q_exp": s["q_exp"], "distinct_days": len(s["days"]),
                      "hover": s["hover"], "click": s["click"], "first_seen": s["first_seen"],
                      "s_value": round(s_value, 3),
                      "reason_code": (REASON_IGNORED if state == "exempt" else
                                      (REASON_COOLDOWN if REASON_COOLDOWN in reasons else "|".join(reasons[:3]))),
                      "blocked_by": reasons,
                      "override": o}
    return out


def recompute(conn, engine_params, states, window_days=WINDOW_DAYS):
    """重算投影并落表（可反复重算，结果应完全一致——35 号 T8 要求）。"""
    ensure_tables(conn)
    res = evaluate(conn, engine_params, states, window_days)
    now = int(time.time())
    conn.execute("DELETE FROM familiarity_projection")
    for lemma, r in res.items():
        conn.execute(
            "INSERT INTO familiarity_projection(lemma, state, q_exp, distinct_days, first_seen,"
            " last_hover_at, decided_at, reason_code, rule_version) VALUES(?,?,?,?,?,?,?,?,?)",
            (lemma, r["state"], r["q_exp"], r["distinct_days"], r["first_seen"], None, now,
             r["reason_code"], RULE_VERSION))
    conn.commit()
    return res


def exempt_set(conn):
    ensure_tables(conn)
    return [r[0] for r in conn.execute("SELECT lemma FROM familiarity_projection WHERE state='exempt'")]


def shadow_report(conn, limit=20):
    """shadow 标定用：谁会被豁免、为什么、以及差点被豁免的（用于估误伤）。"""
    ensure_tables(conn)
    rows = list(conn.execute(
        "SELECT lemma, state, q_exp, distinct_days, reason_code, decided_at FROM familiarity_projection"
        " ORDER BY (state='exempt') DESC, q_exp DESC LIMIT ?", (int(limit),)))
    return {"rule_version": RULE_VERSION, "qualified_page_ms": QUALIFIED_PAGE_MS,
            "window_days": WINDOW_DAYS, "min_exposures": R1_MIN_EXPOSURES, "min_days": R2_MIN_DAYS,
            "would_exempt": [{"lemma": r[0], "q_exp": r[2], "days": r[3], "reason": r[4]}
                             for r in rows if r[1] == "exempt"],
            "near_miss": [{"lemma": r[0], "q_exp": r[2], "days": r[3], "blocked_by": r[4]}
                          for r in rows if r[1] != "exempt"][:10],
            "note": "shadow 模式：这里列出的词**当前仍会被正常注解**，只是记录「如果启用会豁免谁」"}


def restore(conn, lemma, actor="user"):
    """一键恢复：写 revoke 覆盖（R7 冷却 30 天），可选。"""
    ensure_tables(conn)
    conn.execute("INSERT INTO familiarity_override(lemma, action, ts, source) VALUES(?,?,?,?)"
                 " ON CONFLICT(lemma) DO UPDATE SET action=excluded.action, ts=excluded.ts, source=excluded.source",
                 (lemma, "revoke", int(time.time()), actor))
    conn.commit()
    return {"lemma": lemma, "restored": True, "cooldown_days": R7_REVOKE_COOLDOWN_S // 86400}