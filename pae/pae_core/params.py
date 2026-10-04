# -*- coding: utf-8 -*-
"""PAE 运行参数与参数提案（「AI 影响变量」的落地）。

设计：
- DEFAULTS（state.py，冻结）是基准；params 表是运行时覆盖层；有效参数 = DEFAULTS 加覆盖。
- 算法一行不改：fold/state 本来就是参数化函数，改参数 = 改行为，不是改算法。
- 提案过四道守门：结构 → 取值范围（RANGES）→ 冲突（同键冷却）→ 生效并留痕（decisions）。
- 支持 ttl：到期自动回滚（读参数时惰性执行）。
"""
import json
import time
import uuid

from .persona import load_params  # 人格自改守门需要读当前人格
from .state import DEFAULTS, RANGES

# 算法旋钮（来自冻结的 state.py RANGES）+ 引擎级旋钮（注解门槛/密度/主动上限）
ENGINE_KNOBS = {
    "annotate_max_s": (0.50, 0.95),   # 注解门槛：S 超过它就不再注解
    "max_ann_per_page": (8, 80),      # 每页注解上限
    "daily_say_cap": (0, 50),         # 角色每日主动说话上限
}
def _cfg_defaults():
    """引擎旋钮的**初始值**来自 config.json（唯一真相源）；
    运行时改动走 params 表（覆盖层）。这样不再有「一半读 config 一半读表」的双源问题。"""
    try:
        c = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        return {"annotate_max_s": float(c.get("annotate_max_s", 0.75)),
                "max_ann_per_page": int(c.get("max_ann_per_page", 40)),
                "daily_say_cap": int(c.get("daily_say_cap", 10))}
    except Exception:
        return {"annotate_max_s": 0.75, "max_ann_per_page": 40, "daily_say_cap": 10}


ENGINE_DEFAULTS = _cfg_defaults()

# state.py 的 RANGES 是**冻结节点的数据**（改它就破坏 T13 哈希），但它没有覆盖全部决定性常量。
# 这里补上「写死但决定性」的几个——state.py 一行不动，自由度在覆盖层补齐（2026-10-02 终检发现）。
EXTRA_RANGES = {
    # 只有**真接线**的才放这里（s_value 真的读它）
    "prior_free": (0, 10),        # 先验自由次数：几次遭遇内不衰减先验（决定「多久算真的没见过」）
}

# ---------------------------------------------------------------------------
# 规格声明但**当前未接线**的常量（2026-10-02 终检发现）。
# 为什么要单列：可调面必须等于接线面。一个「AI 能调、审计记已生效、但行为毫无变化」的旋钮，
# 是审计账本里的谎话（同类问题此前修过一次：max_ann_per_page 死旋钮）。
# 想真正打开某个，必须先把它接进生产代码，再从这里移出去。
# ---------------------------------------------------------------------------
SPEC_ONLY = {
    "band_p_lo": "规格是「学习带按分位数取」，当前实现用绝对阈值 band_lo/band_hi；改分位数会改行为，需标定后再接",
    "band_p_hi": "同上",
    "eps_active": "规格里的「活跃」阈值——引擎当前没有活跃度门控实现",
    "N_min": "规格里的「带人口最小样本量」——当前未实现最小样本门控",
    "late_amortize_K": "规格里的迟到遭遇摊销——未实现",
    "drift_delta": "0.01 漂移量——未实现",
    "w_quiz": "测验权重——红线禁止提问，永无 quiz 事件（fold 有分支但永不触发）",
}

_ALL_RANGED = tuple(RANGES.keys()) + tuple(ENGINE_KNOBS.keys()) + tuple(EXTRA_RANGES.keys())
# 可调面 = 已接线面（排除 SPEC_ONLY，避免出现「假旋钮」）
TUNABLE = tuple(k for k in _ALL_RANGED if k not in SPEC_ONLY)
RANGE_ALL = dict(RANGES)
RANGE_ALL.update(ENGINE_KNOBS)
RANGE_ALL.update(EXTRA_RANGES)
for _k in SPEC_ONLY:
    RANGE_ALL.pop(_k, None)
DEFAULTS_ALL = dict(DEFAULTS)
DEFAULTS_ALL.update(ENGINE_DEFAULTS)
# ---- 有界自主（Bounded Autonomy）----
# 高影响变量：直接改变 S 值语义或整体注解密度。AI 可以动，但必须带 ttl 且步长受限、每天限一次。
HIGH_IMPACT = ("half_life_days", "E0", "p0", "prior_tau", "rho", "day_bonus",
               "w_hover", "w_known", "eps_active", "N_min", "band_lo", "band_hi")
HIGH_IMPACT_MAX_STEP_FRAC = 0.2   # 单次最多移动变量区间跨度的 20%
HIGH_IMPACT_DAILY_LIMIT = 1       # 全局每天最多 1 次高影响变更

# 自约束型旋钮：限制「AI 自己」的额度。AI **只能调低**（收紧自己），调高需用户操作。
SELF_BINDING = ("daily_say_cap",)
# 人格漂移上限：情绪轴离种子值的最大偏移（可演化，但不能变成另一个人）
PERSONA_SEED = {"warmth": 0.7, "verbosity": 0.3, "humor": 0.4, "formality": 0.2, "proactivity": 0.5}
PERSONA_DRIFT_MAX = 0.25

PROPOSE_COOLDOWN_S = 86400
MAX_PATCH_ITEMS = 8


def _now():
    return int(time.time())


def revert_expired(conn):
    """惰性回滚：把已到期的覆盖删掉。返回被回滚的键列表。"""
    rows = list(conn.execute(
        "SELECT key, decision_id FROM params WHERE expires_at IS NOT NULL AND expires_at <= ?",
        (_now(),)))
    if not rows:
        return []
    for key, did in rows:
        conn.execute("DELETE FROM params WHERE key = ?", (key,))
        if did:
            conn.execute(
                "UPDATE decisions SET effect_result = COALESCE(effect_result, ?) WHERE decision_id = ?",
                ("auto_reverted_ttl", did))
    conn.commit()
    return [k for k, _ in rows]


def effective(conn):
    """DEFAULTS 加运行时覆盖（顺带回滚到期项）。"""
    revert_expired(conn)
    p = dict(DEFAULTS_ALL)
    for key, value in conn.execute("SELECT key, value FROM params"):
        try:
            p[key] = json.loads(value)
        except Exception:
            pass
    return p


def current_overrides(conn):
    out = {}
    for key, value, actor, did, exp in conn.execute(
            "SELECT key, value, actor, decision_id, expires_at FROM params"):
        try:
            v = json.loads(value)
        except Exception:
            v = value
        out[key] = {"value": v, "actor": actor, "decision_id": did, "expires_at": exp}
    return out


def _in_range(key, value):
    rng = RANGE_ALL.get(key)
    if not rng:
        return False, "no range registered"
    lo, hi = rng
    try:
        v = float(value)
    except Exception:
        return False, "not a number"
    if isinstance(DEFAULTS.get(key), int) and float(DEFAULTS.get(key)).is_integer():
        v = int(v)
    if not (float(lo) <= float(v) <= float(hi)):
        return False, "out of range [%s, %s]" % (lo, hi)
    return True, v


def _today_high_impact_count(conn):
    """今天已经生效的高影响变更次数（全局，不分 actor）。"""
    since = _now() // 86400 * 86400
    n = 0
    for (patch, ) in conn.execute("SELECT patch FROM decisions WHERE ts >= ? AND applied = 1", (since,)):
        try:
            items = json.loads(patch or "[]")
        except Exception:
            items = []
        if any(isinstance(i, dict) and i.get("key") in HIGH_IMPACT for i in items):
            n += 1
    return n


def propose(conn, actor, patch, rationale="", evidence=None, ttl_days=None):
    """提交一单参数变更。返回 (ok, body)。

    有界自主规则：
    - **非 user 的 actor 必须带 ttl_days**（AI 的改动一律临时；永久化只能由用户做）
    - 高影响变量额外限制：步长 ≤ 区间跨度的 20%，且全局每天最多 1 次
    """
    did = "dec_" + uuid.uuid4().hex[:16]
    gate = []
    is_user = actor in ("user", "local")   # local = 本机用户（面板/扩展）；AI actor 才受限

    if not isinstance(patch, list) or not patch:
        return False, {"decision_id": did, "applied": False, "gate_result": "structure: patch 必须是非空数组"}
    if len(patch) > MAX_PATCH_ITEMS:
        return False, {"decision_id": did, "applied": False,
                       "gate_result": "structure: 单次提案最多 %d 项" % MAX_PATCH_ITEMS}
    if not (rationale or "").strip():
        return False, {"decision_id": did, "applied": False,
                       "gate_result": "structure: 必须给 rationale（为什么要改）"}
    gate.append("structure: ok")

    accepted = []
    for item in patch:
        if not isinstance(item, dict) or "key" not in item or "to" not in item:
            return False, {"decision_id": did, "applied": False, "gate_result": "structure: 每项需 {key, to}"}
        key = item["key"]
        if key in SPEC_ONLY:
            return False, {"decision_id": did, "applied": False,
                           "gate_result": "spec_only: %s 是规格声明但**当前未接线**，改了不会有任何行为变化。理由：%s"
                                          % (key, SPEC_ONLY[key])}
        if key not in TUNABLE:
            return False, {"decision_id": did, "applied": False,
                           "gate_result": "tunable: %s 不在可调清单（只允许 %s）" % (key, ", ".join(TUNABLE))}
        ok, v = _in_range(key, item["to"])
        if not ok:
            return False, {"decision_id": did, "applied": False, "gate_result": "range: %s %s" % (key, v)}
        accepted.append((key, v))
    gate.append("range: ok")

    # 门2b：AI 的改动必须可回滚（带 ttl）；永久化只能由用户做
    if not is_user and not ttl_days:
        return False, {"decision_id": did, "applied": False,
                       "gate_result": "autonomy: 非 user 的提案必须带 ttl_days（AI 的改动一律临时，永久化请由用户决定）"}
    # 门2b1（M3，评审 2026-10-03）：ttl 无上限 = 100 年后过期 ≈ 永久化，穿透门2b。
    # 非 user 的 ttl 封顶 30 天（与高影响键「每日1次」的保守度对齐）。
    if not is_user and ttl_days:
        try:
            ttl_days = int(ttl_days)
        except Exception:
            return False, {"decision_id": did, "applied": False,
                           "gate_result": "autonomy: ttl_days 必须是整数天数"}
        if ttl_days > 30:
            return False, {"decision_id": did, "applied": False,
                           "gate_result": "autonomy: 非 user 的 ttl_days 封顶 30 天（当前 %d）——永久化请由用户操作" % ttl_days}
        if ttl_days < 1:
            return False, {"decision_id": did, "applied": False,
                           "gate_result": "autonomy: ttl_days 至少 1 天"}
    # 门2b2：自约束型旋钮——AI 只能收紧自己，不能放宽
    if not is_user:
        for key, v in accepted:
            if key in SELF_BINDING:
                cur = conn.execute("SELECT value FROM params WHERE key = ?", (key,)).fetchone()
                cur_v = json.loads(cur[0]) if cur else DEFAULTS_ALL.get(key)
                if float(v) > float(cur_v):
                    return False, {"decision_id": did, "applied": False,
                                   "gate_result": "self-binding: %s 是自我约束项，AI 只能调低（%s→%s 是放宽，需用户操作）"
                                                  % (key, cur_v, v)}
    # 门2c：高影响变量的步长与每日额度
    hi_keys = [k for k, _ in accepted if k in HIGH_IMPACT]
    if hi_keys:
        for key, v in accepted:
            if key not in HIGH_IMPACT:
                continue
            lo, hi = RANGE_ALL[key]
            cur = conn.execute("SELECT value FROM params WHERE key = ?", (key,)).fetchone()
            cur_v = json.loads(cur[0]) if cur else DEFAULTS_ALL.get(key)
            span = float(hi) - float(lo)
            if span > 0 and abs(float(v) - float(cur_v or 0)) > span * HIGH_IMPACT_MAX_STEP_FRAC:
                return False, {"decision_id": did, "applied": False,
                               "gate_result": "impact: %s 单次步长过大（%s→%s，允许跨度 %.1f 的 %.0f%%）"
                                              % (key, cur_v, v, span, HIGH_IMPACT_MAX_STEP_FRAC * 100)}
        used = _today_high_impact_count(conn)
        if used >= HIGH_IMPACT_DAILY_LIMIT:
            return False, {"decision_id": did, "applied": False,
                           "gate_result": "impact: 今日高影响变更已达上限（%d/%d），明天再试"
                                          % (used, HIGH_IMPACT_DAILY_LIMIT)}
        gate.append("impact: high-impact ok (step<=20%%, daily<=%d)" % HIGH_IMPACT_DAILY_LIMIT)

    now = _now()
    for key, _ in accepted:
        row = conn.execute(
            "SELECT ts FROM decisions WHERE actor = ? AND applied = 1 AND patch LIKE ? ORDER BY ts DESC LIMIT 1",
            (actor, "%" + key + "%")).fetchone()
        if row and now - row[0] < PROPOSE_COOLDOWN_S:
            return False, {"decision_id": did, "applied": False,
                           "gate_result": "conflict: %s 在冷却期内（%d 秒前改过同键）" % (key, now - row[0])}
    gate.append("conflict: ok")

    before = {}
    after = {}
    exp = (now + int(ttl_days) * 86400) if ttl_days else None
    for key, v in accepted:
        b = conn.execute("SELECT value FROM params WHERE key = ?", (key,)).fetchone()
        before[key] = json.loads(b[0]) if b else DEFAULTS_ALL.get(key)
        conn.execute(
            "INSERT INTO params(key, value, updated_at, actor, decision_id, expires_at) VALUES(?,?,?,?,?,?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at, actor=excluded.actor, decision_id=excluded.decision_id, expires_at=excluded.expires_at",
            (key, json.dumps(v), now, actor, did, exp))
        after[key] = v
    conn.execute(
        "INSERT INTO decisions(decision_id, ts, actor, capability, patch, rationale, evidence, before, after, applied, gate_result) VALUES(?,?,?,?,?,?,?,?,?,1,?)",
        (did, now, actor, "pae.propose", json.dumps(patch, ensure_ascii=False), rationale,
         json.dumps(evidence or [], ensure_ascii=False), json.dumps(before, ensure_ascii=False),
         json.dumps(after, ensure_ascii=False), " | ".join(gate)))
    conn.commit()
    return True, {"decision_id": did, "applied": True, "applied_keys": after,
                  "expires_at": exp, "gate_result": " | ".join(gate)}




# ---- 人格自改守门（AI 能微调情绪轴，不能改写自己的身份）----
PERSONA_TRAIT_MAX_STEP = 0.05      # 单轴单次最多 ±0.05
PERSONA_TRAIT_COOLDOWN_S = 7 * 86400  # 同一轴 7 天内只能被 AI 动一次


def persona_guard(conn, actor, new_params):
    """校验 actor 能否这样改人格。返回 (ok, issues, changed_axes)。

    - user：不受限（但记录）
    - 其他 actor：只允许改 traits，单轴 ±0.05，同轴 7 天冷却；改名/口癖/设定语/禁忌一律拒绝
    """
    cur = load_params()
    axes = {}
    for axis in ("warmth", "verbosity", "humor", "formality", "proactivity"):
        a = float((cur.get("traits") or {}).get(axis, 0.5))
        b = float((new_params.get("traits") or {}).get(axis, a))
        if abs(b - a) > 1e-9:
            axes[axis] = {"from": a, "to": b}
    if actor in ("user", "local"):   # local = 本机用户（面板/扩展），不受限
        return True, [], axes
    # 非用户：身份字段一律不许动。
    # M2 修复（评审 2026-10-03）：原判据 `field in new_params and != cur` 意味着**省略即绕过**
    # ——LLM 回显参数时丢掉 address_user/style_notes，save 整文件替换后身份字段变 None。
    # 现在：非 user 提交必须显式回传全部身份字段且等于现值，缺一个就拒。
    for field in ("name", "address_user", "backstory_seed", "style_notes", "quirks", "forbidden"):
        if field not in new_params:
            return False, ["identity: %s 缺失——非用户提交必须完整回传全部身份字段（防省略静默清空）" % field], axes
        if new_params.get(field) != cur.get(field):
            return False, ["identity: %s 属于身份设定，AI 不得改写（只有 user 能改）" % field], axes
    for axis, d in axes.items():
        seed = PERSONA_SEED.get(axis, d["from"])
        if abs(d["to"] - seed) > PERSONA_DRIFT_MAX + 1e-9:
            return False, ["drift: %s 距种子值 %.2f 已超 %.2f（能演化，但不能变成另一个人）"
                           % (axis, seed, PERSONA_DRIFT_MAX)], axes
        if abs(d["to"] - d["from"]) > PERSONA_TRAIT_MAX_STEP + 1e-9:
            return False, ["step: %s 单次变化 %.3f 超过 ±%.2f" % (axis, abs(d["to"] - d["from"]),
                                                              PERSONA_TRAIT_MAX_STEP)], axes
        since = int(time.time()) - PERSONA_TRAIT_COOLDOWN_S
        row = conn.execute(
            "SELECT ts FROM decisions WHERE actor = ? AND ts >= ? AND patch LIKE ? ORDER BY ts DESC LIMIT 1",
            (actor, since, "%" + axis + "%")).fetchone()
        if row:
            return False, ["cooldown: %s 在 7 天冷却期内（AI 每天格调都不一样会很怪）" % axis], axes
    return True, [], axes


def list_decisions(conn, actor=None, n=20):
    sql = ("SELECT decision_id, ts, actor, capability, patch, rationale, before, after, applied,"
           " gate_result, effect_result FROM decisions")
    args = []
    if actor:
        sql += " WHERE actor = ?"
        args.append(actor)
    sql += " ORDER BY ts DESC LIMIT ?"
    args.append(int(n))
    out = []
    for r in conn.execute(sql, args):
        out.append({"decision_id": r[0], "ts": r[1], "actor": r[2], "capability": r[3],
                    "patch": json.loads(r[4] or "[]"), "rationale": r[5],
                    "before": json.loads(r[6] or "{}"), "after": json.loads(r[7] or "{}"),
                    "applied": bool(r[8]), "gate_result": r[9], "effect_result": r[10]})
    return out