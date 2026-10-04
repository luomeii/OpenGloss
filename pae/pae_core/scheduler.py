# -*- coding: utf-8 -*-
"""PAE 低频调度：让内置角色按节奏「活着」，同时被所有边界保护住。

设计（保守优先）：
- **默认关闭**（config.json 的 agent_schedule.enabled=false）——AI 主动行为由用户决定何时开启
- 触发条件全是有依据的：距上次 ≥ interval_min、期间有新学习事件 ≥ min_new_events、限流/日额度未到
- 没有任何新活动就不跑 → **不花 LLM 额度、也不写日志噪音**
- 线程用自己的 SQLite 连接（WAL 下多连接安全），不干扰请求处理
"""
import json
import threading
import time
from pathlib import Path

import os

ROOT = Path(__file__).resolve().parents[1]
# 允许独立配置文件（测试用；生产就是 pae/config.json）
CONFIG = Path(os.environ.get("PAE_SCHEDULE_CONFIG") or (ROOT / "config.json"))
CHECK_INTERVAL_S = 60

_state = {"thread": None, "stop": False, "last_check": 0, "last_result": None}
AUTO_STIMULUS = "（自动巡检：没有用户提问，你自己看状态决定要不要做点什么。没必要时 actions 留空，别为了显得有用而行动。）"


def config():
    """每次都重读 config.json —— 开启/关闭不需要重启引擎。"""
    try:
        return (json.loads(CONFIG.read_text(encoding="utf-8")).get("agent_schedule") or {})
    except Exception:
        return {}


def should_tick(conn, now=None):
    """返回 (是否该跑, 原因)。纯 SQL，不花任何额度。"""
    from . import agent as agent_mod
    cfg = config()
    if not cfg.get("enabled"):
        return False, "disabled"
    now = int(now or time.time())
    row = conn.execute("SELECT MAX(ts) FROM agent_log").fetchone()
    last = (row or [None])[0] or 0
    if now - last < agent_mod.TICK_MIN_INTERVAL_S:
        return False, "too_soon"
    if now - last < int(cfg.get("interval_min", 30)) * 60:
        return False, "interval"
    day_start = now // 86400 * 86400
    today = conn.execute("SELECT COUNT(*) FROM agent_log WHERE ts >= ? AND result NOT LIKE '%rate_limited%'",
                         (day_start,)).fetchone()[0]
    if today >= agent_mod.TICK_DAILY_LIMIT:
        return False, "daily_limit"
    new_events = conn.execute("SELECT COUNT(*) FROM events WHERE ts > ?", (last,)).fetchone()[0]
    if new_events < int(cfg.get("min_new_events", 3)):
        return False, "no_new_activity"
    return True, "ok"


def maybe_tick(conn):
    ok, why = should_tick(conn)
    if not ok:
        _state["last_result"] = {"skipped": why, "ts": int(time.time())}
        return {"skipped": why}
    from . import agent as agent_mod
    out = agent_mod.tick(conn, actor="companion", stimulus=AUTO_STIMULUS)
    _state["last_result"] = {"ran": True, "ts": int(time.time()), "error": out.get("error"),
                             "actions": [a.get("name") for a in (out.get("actions") or [])]}
    return out


def status(conn):
    from . import agent as agent_mod
    cfg = config()
    row = conn.execute("SELECT MAX(ts) FROM agent_log").fetchone()
    last = (row or [None])[0]
    day_start = int(time.time()) // 86400 * 86400
    today = conn.execute("SELECT COUNT(*) FROM agent_log WHERE ts >= ? AND result NOT LIKE '%rate_limited%'",
                         (day_start,)).fetchone()[0]
    ok, why = should_tick(conn)
    return {"config": cfg, "enabled": bool(cfg.get("enabled")), "last_tick_ts": last,
            "ticks_today": today, "daily_limit": agent_mod.TICK_DAILY_LIMIT,
            "min_interval_s": agent_mod.TICK_MIN_INTERVAL_S,
            "would_tick_now": ok, "reason": why, "last_result": _state.get("last_result"),
            "thread_alive": bool(_state.get("thread") and _state["thread"].is_alive())}


def _loop(db_path):
    from .db import get_db
    conn = get_db(db_path)
    while not _state["stop"]:
        try:
            _state["last_check"] = int(time.time())
            maybe_tick(conn)
        except Exception as e:
            _state["last_result"] = {"error": "%s: %s" % (type(e).__name__, str(e)[:120]),
                                     "ts": int(time.time())}
        for _ in range(CHECK_INTERVAL_S):
            if _state["stop"]:
                break
            time.sleep(1)


def start(db_path):
    if _state.get("thread") and _state["thread"].is_alive():
        return False
    _state["stop"] = False
    t = threading.Thread(target=_loop, args=(str(db_path),), daemon=True, name="pae-scheduler")
    t.start()
    _state["thread"] = t
    return True


def stop():
    _state["stop"] = True