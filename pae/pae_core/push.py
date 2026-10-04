# -*- coding: utf-8 -*-
"""PAE 推送通道：引擎主动给页面发消息（没有它，proactivity 这个参数没有意义）。

- BUS：进程内广播（SSE 订阅者各自一个队列）
- say/hint 都落 push_log（用户能看见「AI 对我说过什么」），并受 daily_say_cap 约束
"""
import json
import queue
import threading
import time
from pathlib import Path


class _Broadcaster:
    def __init__(self):
        self._subs = []
        self._lock = threading.Lock()

    def subscribe(self):
        q = queue.Queue(maxsize=200)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def publish(self, event):
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(event)
            except Exception:
                pass

    def subscriber_count(self):
        with self._lock:
            return len(self._subs)


BUS = _Broadcaster()


def _day_start(now=None):
    """自然日边界（沿用既有的 UTC 分日语义，不引入时区变量）。

    允许显式传入 now：主动说话的条件判定与测试都需要一条可复现的时间线，
    「第 N 条」这类判断必须和写日志时的 ts 用**同一个** now，否则跨日判断会漂。
    """
    return int(now if now is not None else time.time()) // 86400 * 86400


def _today_start():
    return _day_start()


def today_count(conn, kind, now=None):
    return conn.execute("SELECT COUNT(*) FROM push_log WHERE kind = ? AND ts >= ? AND blocked = 0",
                        (kind, _day_start(now))).fetchone()[0]


def _engine_write_lock(conn):
    """H3/M5 修复（评审 2026-10-03）：共享 SQLite 连接上事务边界属于连接不属于线程，
    多线程并发写会互相提交对方事务（提前落地）并撞语句缓存产生 API misuse 硬异常。
    写路径统一过引擎锁——engine 侧 record_event/annotate 已是这个纪律，push 侧补齐。
    拿不到引擎（独立测试库）就退回无锁（单线程测试安全）。"""
    try:
        from .engine import get_engine
        if get_engine().conn is conn:
            return get_engine().lock
    except Exception:
        pass
    return None


class _NullLock:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


_NULL_LOCK = _NullLock()


def _write_lock(conn):
    lk = _engine_write_lock(conn)
    return lk if lk is not None else _NULL_LOCK


def _log(conn, kind, payload, actor, blocked=False, reason="", ts=None):
    with _write_lock(conn):
        conn.execute("INSERT INTO push_log(ts, kind, payload, actor, blocked, reason) VALUES(?,?,?,?,?,?)",
                     (int(ts if ts is not None else time.time()), kind,
                      json.dumps(payload, ensure_ascii=False), actor,
                      1 if blocked else 0, reason))
        conn.commit()


def say(conn, actor, text, anchor="", cap=None, meta=None, ts=None):
    """往页面说一句。受每日上限约束（默认取 params 的 daily_say_cap）。

    meta：附加到推送事件与日志 payload 里的标签（主动说话用它标记 proactive=true，
          这样「今天主动说了几条」能直接查账，不必另开表）。
    ts：仅测试/离线回放用；生产走真实时间。
    """
    text = (text or "").strip()
    if not text:
        return False, {"error": "empty_text"}
    if len(text) > 300:
        return False, {"error": "text_too_long", "max": 300}
    if cap is None:
        from . import params as params_mod
        cap = int(params_mod.effective(conn).get("daily_say_cap", 10))
    # M5 修复（评审 2026-10-03）：count 与 insert 必须同一把锁，否则双线程并发
    # 各自读到 n<cap → 双发（超发配额）。锁与 _log 同源（引擎锁），单线程退无锁。
    with _write_lock(conn):
        n = today_count(conn, "say", now=ts)
        if n >= cap:
            conn.execute("INSERT INTO push_log(ts, kind, payload, actor, blocked, reason)"
                         " VALUES(?,?,?,?,?,?)",
                         (int(ts if ts is not None else time.time()), "say",
                          json.dumps({"text": text, "anchor": anchor}, ensure_ascii=False),
                          actor, 1, "daily_cap"))
            conn.commit()
            return False, {"error": "daily_cap_reached", "cap": cap, "sent_today": n,
                           "hint": "用户可调 daily_say_cap 或一键静音"}
        ev = {"type": "say", "text": text, "anchor": anchor, "actor": actor,
              "ts": int(ts if ts is not None else time.time())}
        if meta:
            ev.update(meta)
        conn.execute("INSERT INTO push_log(ts, kind, payload, actor, blocked, reason) VALUES(?,?,?,?,?,?)",
                     (ev["ts"], "say", json.dumps(ev, ensure_ascii=False), actor, 0, ""))
        conn.commit()
    BUS.publish(ev)
    return True, {"delivered": True, "subscribers": BUS.subscriber_count(), "sent_today": n + 1, "cap": cap}


def hint(conn, actor, lemma, level="up"):
    """在页面上强化/弱化某词（不改 S，只改本次渲染）。"""
    if level not in ("up", "down"):
        return False, {"error": "bad_level", "allowed": ["up", "down"]}
    ev = {"type": "hint", "lemma": lemma, "level": level, "actor": actor, "ts": int(time.time())}
    _log(conn, "hint", ev, actor)
    BUS.publish(ev)
    return True, {"delivered": True, "subscribers": BUS.subscriber_count()}


def notify(conn, kind, payload, actor="system"):
    """参数变更/决策回滚等系统事件也走同一条推送（角色能告知「我把密度调低了」）。"""
    ev = dict(payload or {})
    ev["type"] = kind
    ev["ts"] = int(time.time())
    _log(conn, kind, ev, actor)
    BUS.publish(ev)
    return ev


def log(conn, n=20, kind=None):
    sql = "SELECT ts, kind, payload, actor, blocked, reason FROM push_log"
    args = []
    if kind:
        sql += " WHERE kind = ?"
        args.append(kind)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(int(n))
    out = []
    for r in conn.execute(sql, args):
        try:
            p = json.loads(r[2] or "{}")
        except Exception:
            p = {}
        out.append({"ts": r[0], "kind": r[1], "payload": p, "actor": r[3],
                    "blocked": bool(r[4]), "reason": r[5]})
    return out


# ---------------------------------------------------------------------------
# 主动说话（问题一修复，2026-10-03）：规则版自发触发 —— 非 LLM、不花钱
#
# 为什么需要它：链路本身早就通了
#   push.say → BUS → GET /v1/ext/stream(SSE) → sw.js paeBroadcast → content.js PAE_PUSH → 角色壳
# 但引擎侧**没有任何生产者会自发 emit say**：唯一入口 pae.say 是能力面调用（要外部调用者），
# agent.tick 是真实 LLM 且 agent_schedule.enabled 默认 false。
# 当时实测：两天使用量下 push_log 0 行、agent_log 0 行 → 她一次都没主动开口。
#
# 触发条件（**全部**满足才发，逐条对应产品要求）：
#   ① 自上次主动说话以来，新曝光（events.type='encounter'）累计 ≥ PROACTIVE_MIN_EXPOSURES(120)
#      —— 约几页阅读量；避免刚打开页面就搭话
#   ② 距上次主动说话 ≥ PROACTIVE_MIN_GAP_S(2 小时)
#   ③ 今日主动条数 < PROACTIVE_DAILY_MAX(3) —— 刻意低于 daily_say_cap(10)，宁少勿多
#   ④ 今日 say 总额度未用尽（daily_say_cap；用户可调，AI 只能调低）
#   ⑤ 未静音（push_log 里最新一条 kind='mute' 的 muted 字段；见 set_muted/is_muted）
#   ⑥ 挑得出词：从「最近在啃」（band_words 按 last_seen 降序取最近接触的 60 个）里选 S 涨得最快的
#      —— S 随遭遇单调累积，故按 (s_value 大 → 遭遇多 → lemma) 排序，即「积攒最多、最接近出带」。
#      池子必须取 60 而不是 12：最近 12 个词**往往全是只见过 1 次的新词**（每页都不断冒新词），
#      而见过 ≥3 次、S 已逼近带顶的词（如 something 4 次 0.549 / chat 8 次 0.547）会被挤出前 12；
#      只取前 12 就等于永远挑到刚碰到的生词，「快出带了」会变成一句假话。
#      主池 = 遭遇 ≥3 次（真的在积累）；冷启动（压根没有这种词）才退到 ≥1 次里挑 S 最高的那个。
#
# 命中后用模板生成一句话（PROACTIVE_TEMPLATE，不调 LLM），走**同一条** say 通道：
# 同一个每日上限、同一份 push_log 审计，用户侧与手动 say 完全无法区分待遇差别。
# 评估节流：annotate 里每 PROACTIVE_EVAL_INTERVAL_S(300) 秒最多真正评估一次（纯 SQL，开销可忽略）。
# ---------------------------------------------------------------------------
# 2026-10-03 二次反馈：120 次 + 2 小时太难触发（两天实际使用一次都没见过）→ 60 次 + 45 分钟。
# 三次反馈：一天只见到了 1 次，想再多一点 → 45 次 + 30 分钟。
# 真正的安全阀是日上限（3 条 + 其中 LLM ≤2 条）：阈值只决定「今天这 3 条多早出现」，不会变多。
PROACTIVE_MIN_EXPOSURES = 45
PROACTIVE_MIN_GAP_S = 30 * 60
PROACTIVE_DAILY_MAX = 3
PROACTIVE_EVAL_INTERVAL_S = 300
PROACTIVE_TEMPLATE = "%s 见了 %d 次了，快出带了"

# ---------------------------------------------------------------------------
# LLM 版主动说话（2026-10-03 二次反馈）：用户反馈「始终只有正则式规则对话，太单调，
# 真正调用说话的智能在哪」。规则版保留兜底，命中触发时优先让 LLM 带着**她的人格 +
# 近期对话上下文**说一句（这才是"真的在说话"）；LLM 失败/超额一律退回模板句，零风险。
# 限流：每天最多 PROACTIVE_LLM_DAILY_MAX(2) 条走 LLM（真实花费，宁少勿多）；
# config.json 的 proactive_llm=false 可整体关掉（测试也用它做确定性隔离）。
# ---------------------------------------------------------------------------
PROACTIVE_LLM_ENABLED = True
PROACTIVE_LLM_DAILY_MAX = 2


def _cfg(key, default):
    try:
        return json.loads((Path(__file__).resolve().parents[1] / "config.json")
                          .read_text(encoding="utf-8")).get(key, default)
    except Exception:
        return default


def _llm_enabled():
    """LLM 主动说话总开关：config proactive_llm 优先；模块变量兜底（测试可改）。"""
    if not PROACTIVE_LLM_ENABLED:
        return False
    try:
        v = _cfg("proactive_llm", True)
        return bool(v)
    except Exception:
        return True


def _proactive_llm_count(conn, since_ts):
    """今天走了 LLM 的主动说话条数（payload 里 llm=1）。"""
    sql = "SELECT COUNT(*) FROM push_log WHERE kind='say' AND blocked=0 AND ts >= ?"
    try:
        return int(conn.execute(sql + " AND json_extract(payload,'$.llm') = 1",
                                (int(since_ts),)).fetchone()[0])
    except Exception:
        return int(conn.execute(sql + " AND payload LIKE '%\"llm\": 1%'", (int(since_ts),)).fetchone()[0])


def _proactive_llm_text(conn, picked):
    """让 LLM 带着人格与近期对话说一句。任何异常返回 None（调用方退回模板句）。"""
    from . import llm as llm_mod
    from . import persona as persona_mod
    from . import memory as mem_mod
    p = persona_mod.load_params()
    lemma = str(picked.get("lemma") or "")
    n_enc = int(picked.get("encounters") or 0)
    s_val = float(picked.get("s_value") or 0.0)
    gloss = str(picked.get("gloss") or "")
    cb = ("用户正在读英文网页。词 \"%s\"%s 已遇到 %d 次、熟悉度 S≈%.2f，快出学习带了。"
          % (lemma, ("（释义：%s）" % gloss[:60]) if gloss else "", n_enc, s_val))
    sysp = persona_mod.render(p, context_block=cb)
    history = []
    for t in (mem_mod.recent(conn, None, 8) or []):
        role = "assistant" if str(t.get("role")) in ("agent", "assistant") else "user"
        txt = str(t.get("text") or "").strip()
        if txt:
            history.append({"role": role, "content": txt[:500]})
    msgs = ([{"role": "system", "content": sysp}] + history
            + [{"role": "user", "content":
                "请用一两句中文随口跟用户提一下这个词（可以带点释义或它在页面里的用法感觉），"
                "像顺手帮个忙。不要报任何数字，别说教，不打断阅读节奏，最多两句。"}])
    r = llm_mod.chat(msgs, temperature=0.8, timeout=12, max_tokens=200)
    txt = (r.get("content") or "").strip()
    if not txt or len(txt) > 300:
        return None
    return " ".join(txt.split())[:300]   # 压成单行，气泡/边注里不炸版

_PROACTIVE_STATE = {"last_eval": 0.0, "last_result": None}


def _proactive_count(conn, since_ts):
    """今天（ts >= since_ts）主动说了几条。判定依据：payload 里的 proactive=true。"""
    sql = "SELECT COUNT(*) FROM push_log WHERE kind='say' AND blocked=0 AND ts >= ?"
    try:
        return int(conn.execute(sql + " AND json_extract(payload,'$.proactive') = 1",
                                (int(since_ts),)).fetchone()[0])
    except Exception:   # 老 SQLite 没编 JSON1：退化成文本匹配（我们自己的 json.dumps 形态固定）
        return int(conn.execute(sql + " AND payload LIKE '%\"proactive\": true%'",
                                (int(since_ts),)).fetchone()[0])


def _last_proactive_ts(conn):
    """上次主动说话的 ts（没有则 None）——条件 ① 的计数起点、条件 ② 的间隔起点。"""
    sql = "SELECT MAX(ts) FROM push_log WHERE kind='say' AND blocked=0"
    try:
        return conn.execute(sql + " AND json_extract(payload,'$.proactive') = 1").fetchone()[0]
    except Exception:
        return conn.execute(sql + " AND payload LIKE '%\"proactive\": true%'").fetchone()[0]


def is_muted(conn):
    """用户是否静音过：读最新一条 kind='mute' 标记，没有标记 = 没静音。"""
    try:
        row = conn.execute("SELECT payload FROM push_log WHERE kind='mute'"
                           " ORDER BY id DESC LIMIT 1").fetchone()
    except Exception:
        return False
    if not row:
        return False
    try:
        return bool(json.loads(row[0] or "{}").get("muted"))
    except Exception:
        return False


def set_muted(conn, muted=True, actor="user", ts=None):
    """写下/解除静音标记（落 push_log，可审计：谁在什么时候让她闭嘴了）。

    说明：扩展的「静音」按钮目前只活在 sw.js 内存里（S.muted），重启浏览器即失效、
    也不回传引擎——引擎侧因此必须有自己的标记，否则规则版主动说话会绕过用户意愿。
    """
    _log(conn, "mute", {"muted": bool(muted)}, actor, ts=ts)
    return {"muted": bool(muted)}


PROACTIVE_POOL_SIZE = 60     # 「最近在啃」的取样宽度（见注释 ⑥：12 太窄，全是生词）


def _pick_proactive_word(engine):
    """从「最近在啃」的词里挑 S 涨得最快的（规则排序，不调 LLM）。挑不出返回 None。"""
    from . import observe as obs_mod
    try:
        lock = getattr(engine, "lock", None)
        if lock is not None:
            with lock:                       # 快照式读取：并发折叠时不会「字典改变大小」
                words = obs_mod.band_words(engine, PROACTIVE_POOL_SIZE)
        else:
            words = obs_mod.band_words(engine, PROACTIVE_POOL_SIZE)
    except Exception:
        return None
    pool = [w for w in words if int(w.get("encounters") or 0) >= 3 and not w.get("known_click")]
    if not pool:                             # 冷启动：还没词积累到 3 次，退到「见过一次以上」里挑 S 最高的
        pool = [w for w in words if int(w.get("encounters") or 0) >= 1]
    if not pool:
        return None
    pool.sort(key=lambda w: (-float(w.get("s_value") or 0.0),
                             -int(w.get("encounters") or 0), str(w.get("lemma"))))
    return pool[0]


def _proactive_result(sent, why, now, **extra):
    out = {"sent": bool(sent), "reason": why, "skipped": None if sent else why, "now": int(now)}
    out.update(extra)
    _PROACTIVE_STATE["last_result"] = out
    return out


def maybe_proactive(conn, engine=None, now=None):
    """极轻量自发触发：条件见本段顶部注释。返回 {"sent":bool,...}，**永不抛异常**。

    调用点：engine.annotate() 末尾（节流 5 分钟）；也可直接调用（测试/手动）。
    """
    now = int(now if now is not None else time.time())
    try:
        if now - float(_PROACTIVE_STATE.get("last_eval") or 0.0) < PROACTIVE_EVAL_INTERVAL_S:
            return {"sent": False, "reason": "eval_throttled", "skipped": "eval_throttled", "now": now}
        _PROACTIVE_STATE["last_eval"] = now
        if is_muted(conn):                                                   # 条件 ⑤
            return _proactive_result(False, "muted", now)
        if _proactive_count(conn, _day_start(now)) >= PROACTIVE_DAILY_MAX:   # 条件 ③
            return _proactive_result(False, "daily_max", now)
        from . import params as params_mod
        cap = int(params_mod.effective(conn).get("daily_say_cap", 10))
        if today_count(conn, "say", now=now) >= cap:                         # 条件 ④
            return _proactive_result(False, "quota", now, cap=cap)
        last = _last_proactive_ts(conn)
        if last and now - int(last) < PROACTIVE_MIN_GAP_S:                   # 条件 ②
            return _proactive_result(False, "gap", now, last_proactive_ts=int(last),
                                     wait_s=PROACTIVE_MIN_GAP_S - (now - int(last)))
        exposures = int(conn.execute("SELECT COUNT(*) FROM events WHERE type='encounter' AND ts > ?",
                                     (int(last or 0),)).fetchone()[0])
        if exposures < PROACTIVE_MIN_EXPOSURES:                              # 条件 ①
            return _proactive_result(False, "few_exposures", now, exposures=exposures,
                                     need=PROACTIVE_MIN_EXPOSURES)
        picked = _pick_proactive_word(engine) if engine is not None else None  # 条件 ⑥
        if not picked:
            return _proactive_result(False, "no_word", now, exposures=exposures)
        lemma = str(picked.get("lemma") or "")
        n_enc = int(picked.get("encounters") or 0)
        # LLM 版优先（带人格+近期对话；日限 2 条），失败/超额 → 模板句兜底
        used_llm = None
        text = None
        if _llm_enabled() and _proactive_llm_count(conn, _day_start(now)) < PROACTIVE_LLM_DAILY_MAX:
            try:
                text = _proactive_llm_text(conn, picked)
                used_llm = bool(text)
            except Exception:
                text = None
                used_llm = False
        if not text:
            text = PROACTIVE_TEMPLATE % (lemma, n_enc)
        meta = {"proactive": True, "lemma": lemma, "encounters": n_enc,
                "s_value": picked.get("s_value")}
        if used_llm:
            meta["llm"] = 1
        ok, body = say(conn, "companion", text, anchor=lemma, meta=meta, ts=now)
        return _proactive_result(bool(ok), "sent" if ok else "say_failed", now,
                                 exposures=exposures, text=text, lemma=lemma,
                                 llm=used_llm, body=body)
    except Exception as e:   # 绝不因为「她想说话」把注解主链路拖垮
        return {"sent": False, "reason": "error", "skipped": "error", "now": now,
                "error": "%s: %s" % (type(e).__name__, e)}
