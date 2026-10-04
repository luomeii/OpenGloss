# -*- coding: utf-8 -*-
"""PAE 记忆：事实卡（facts）与会话（chat_sessions / chat_turns）。

设计要点：
- **不写进 events 表**：新增事件类型必须进 fold 的 _OBS_ONLY 才能不影响状态，而 fold 语义冻结——
  所以记忆走独立表（有自己的 created_at / superseded_by 做审计），关引擎重开仍可查询。
- **$schema 写入守卫**：kind 枚举、text ≤200、**evidence 必填（无证据不收）**、role 枚举。
- 淘汰规则：active ≤8（2026-10-03 二次反馈：5 太小，用户记的东西一多就被挤掉）；
  超出时按 (confidence × 新鲜度) 淘汰最弱一条 → 写 superseded_by（永不物理删除）。
"""
import json
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

KINDS = ("goal", "preference", "event", "relation", "vocab_context")
ROLES = ("user", "agent")
MAX_ACTIVE = 8   # 侧栏「它记下的」显示 8 条；config facts_max_active 可覆盖
MAX_FACT_TEXT = 200
MAX_TURN_TEXT = 2000


def _cfg(key, default):
    try:
        return json.loads((ROOT / "config.json").read_text(encoding="utf-8")).get(key, default)
    except Exception:
        return default


def _now():
    return int(time.time())


# ---------- facts ----------

def active_facts(conn, limit=None):
    sql = ("SELECT fact_id, kind, text, source_actor, evidence, confidence, created_at, expires_at"
           " FROM facts WHERE superseded_by IS NULL")
    if limit:
        sql += " ORDER BY created_at DESC LIMIT %d" % int(limit)
    else:
        sql += " ORDER BY created_at DESC"
    out = []
    for r in conn.execute(sql):
        d = {"fact_id": r[0], "kind": r[1], "text": r[2], "source_actor": r[3],
             "confidence": r[5], "created_at": r[6], "expires_at": r[7]}
        try:
            d["evidence"] = json.loads(r[4] or "[]")
        except Exception:
            d["evidence"] = []
        out.append(d)
    return out


def _evict_if_needed(conn):
    limit = int(_cfg("facts_max_active", MAX_ACTIVE))
    act = active_facts(conn)
    if len(act) <= limit:
        return None
    now = _now()

    def score(f):
        age_days = (now - f["created_at"]) / 86400.0
        return f["confidence"] * (1.0 / (1.0 + age_days))

    # **用户写的先不动**：优先淘汰 AI/系统写的事实（用户的事实是人给的，不能被 AI 挤掉）
    user_facts = [f for f in act if f["source_actor"] in ("user", "local")]
    others = [f for f in act if f["source_actor"] not in ("user", "local")]
    pool = others if others else user_facts
    weakest = sorted(pool, key=score)[0]
    conn.execute("UPDATE facts SET superseded_by = ? WHERE fact_id = ?",
                 ("evicted_by_quota", weakest["fact_id"]))
    conn.commit()
    return weakest["fact_id"]


def remember(conn, actor, kind, text, evidence, confidence=0.7, ttl_days=None):
    """写一条长期记忆。返回 (ok, body)。守卫不过直接拒。"""
    kind = (kind or "").strip()
    text = (text or "").strip()
    if kind not in KINDS:
        return False, {"error": "bad_kind", "allowed": list(KINDS)}
    if not text:
        return False, {"error": "empty_text"}
    if len(text) > MAX_FACT_TEXT:
        return False, {"error": "text_too_long", "max": MAX_FACT_TEXT, "len": len(text)}
    if not evidence:
        return False, {"error": "evidence_required",
                       "detail": "无证据不收：evidence 必须指向支撑它的事件/对话"}
    try:
        conf = float(confidence)
    except Exception:
        return False, {"error": "bad_confidence"}
    conf = min(1.0, max(0.0, conf))
    fid = "fact_" + uuid.uuid4().hex[:16]
    exp = (_now() + int(ttl_days) * 86400) if ttl_days else None
    conn.execute(
        "INSERT INTO facts(fact_id, kind, text, source_actor, evidence, confidence, created_at, expires_at)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (fid, kind, text, actor, json.dumps(evidence, ensure_ascii=False), conf, _now(), exp),
    )
    conn.commit()
    evicted = _evict_if_needed(conn)
    return True, {"fact_id": fid, "kind": kind, "evicted": evicted}


def forget(conn, actor, fact_id):
    """撤销一条记忆：不物理删除，只标记被谁取代。**AI 不能抹掉用户写的事实**。"""
    row = conn.execute("SELECT fact_id, superseded_by, source_actor FROM facts WHERE fact_id = ?",
                       (fact_id,)).fetchone()
    if not row:
        return False, {"error": "not_found", "fact_id": fact_id}
    if row[1]:
        return True, {"fact_id": fact_id, "already_superseded_by": row[1], "changed": False}
    if row[2] in ("user", "local") and actor not in ("user", "local"):
        return False, {"error": "protected_fact", "fact_id": fact_id, "owner": row[2],
                       "detail": "这条事实是用户写的，AI 不能抹掉（只有用户能）"}  
    conn.execute("UPDATE facts SET superseded_by = ? WHERE fact_id = ?",
                 ("forgotten_by_" + str(actor), fact_id))
    conn.commit()
    return True, {"fact_id": fact_id, "changed": True}


def search(conn, query, k=5, kind=None):
    q = ("%" + (query or "").strip() + "%")
    sql = ("SELECT fact_id, kind, text, source_actor, evidence, confidence, created_at"
           " FROM facts WHERE superseded_by IS NULL AND text LIKE ?")
    args = [q]
    if kind:
        sql += " AND kind = ?"
        args.append(kind)
    sql += " ORDER BY confidence DESC, created_at DESC LIMIT ?"
    args.append(int(k))
    out = []
    for r in conn.execute(sql, args):
        try:
            ev = json.loads(r[4] or "[]")
        except Exception:
            ev = []
        out.append({"fact_id": r[0], "kind": r[1], "text": r[2], "source_actor": r[3],
                    "evidence": ev, "confidence": r[5], "created_at": r[6]})
    return out


# ---------- sessions ----------

def session_open(conn, actor="companion"):
    sid = "sess_" + uuid.uuid4().hex[:16]
    conn.execute("INSERT INTO chat_sessions(session_id, actor, started_at, turn_count) VALUES(?,?,?,0)",
                 (sid, actor, _now()))
    conn.commit()
    return {"session_id": sid, "actor": actor, "started_at": _now()}


def session_turn(conn, session_id, role, text, refs=None):
    role = (role or "").strip()
    if role not in ROLES:
        return False, {"error": "bad_role", "allowed": list(ROLES)}
    text = (text or "").strip()
    if not text:
        return False, {"error": "empty_text"}
    if len(text) > MAX_TURN_TEXT:
        return False, {"error": "text_too_long", "max": MAX_TURN_TEXT}
    row = conn.execute("SELECT session_id FROM chat_sessions WHERE session_id = ?", (session_id,)).fetchone()
    if not row:
        return False, {"error": "unknown_session", "session_id": session_id}
    tid = "turn_" + uuid.uuid4().hex[:16]
    conn.execute("INSERT INTO chat_turns(turn_id, session_id, role, text, ts, refs) VALUES(?,?,?,?,?,?)",
                 (tid, session_id, role, text, _now(), json.dumps(refs or [], ensure_ascii=False)))
    conn.execute("UPDATE chat_sessions SET turn_count = turn_count + 1 WHERE session_id = ?", (session_id,))
    conn.commit()
    return True, {"turn_id": tid, "session_id": session_id}


def session_close(conn, session_id, summary=None):
    row = conn.execute("SELECT session_id FROM chat_sessions WHERE session_id = ?", (session_id,)).fetchone()
    if not row:
        return False, {"error": "unknown_session", "session_id": session_id}
    conn.execute("UPDATE chat_sessions SET ended_at = ?, summary = COALESCE(?, summary) WHERE session_id = ?",
                 (_now(), summary, session_id))
    conn.commit()
    return True, {"session_id": session_id, "closed": True}


def recent(conn, session_id=None, n=10):
    """最近的对话轮次（关引擎重开仍可取回——这就是「长久记忆」的最小证据）。"""
    sql = ("SELECT t.turn_id, t.session_id, t.role, t.text, t.ts"
           " FROM chat_turns t")
    args = []
    if session_id:
        sql += " WHERE t.session_id = ?"
        args.append(session_id)
    # ts 精度是秒：同一轮「用户一句 + 它一句」落在同一秒里是常态，
    # 只按 ts 排序时同刻行的先后是未定义的（实测会把 user/agent 颠倒 → 多轮上下文串角色）。
    # 加 rowid 兜底 = 写入顺序，reverse 后就是真正的时间正序。
    sql += " ORDER BY t.ts DESC, t.rowid DESC LIMIT ?"
    args.append(int(n))
    rows = list(conn.execute(sql, args))
    rows.reverse()  # 按时间正序返回，读起来自然
    return [{"turn_id": r[0], "session_id": r[1], "role": r[2], "text": r[3], "ts": r[4]} for r in rows]
