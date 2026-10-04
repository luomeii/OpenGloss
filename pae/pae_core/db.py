"""PAE SQLite storage layer: events (append-only) + dict_words (lemma lexicon)."""
import json
import sqlite3
import uuid
from typing import Optional

DDL = """
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    idem_key TEXT UNIQUE,
    ts       INTEGER NOT NULL,
    type     TEXT    NOT NULL,
    payload  TEXT    NOT NULL,
    source   TEXT    NOT NULL DEFAULT 'api'
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);

CREATE TABLE IF NOT EXISTS dict_words (
    word        TEXT PRIMARY KEY,
    translation TEXT,
    exchange    TEXT,
    bnc         INTEGER,
    frq         INTEGER,
    tag         TEXT
);
CREATE INDEX IF NOT EXISTS idx_dict_words_bnc ON dict_words(bnc);
CREATE INDEX IF NOT EXISTS idx_dict_words_frq ON dict_words(frq);

-- 能力面审计：谁在什么时候动了哪项能力（用户要能看见「哪个智能体在动我的系统」）
CREATE TABLE IF NOT EXISTS cap_calls (
    call_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         INTEGER NOT NULL,
    actor      TEXT    NOT NULL,
    capability TEXT    NOT NULL,
    ok         INTEGER NOT NULL,
    detail     TEXT
);
CREATE INDEX IF NOT EXISTS idx_cap_calls_ts ON cap_calls(ts);

-- 记忆三表：引擎不该"关掉就忘了你说过什么"
CREATE TABLE IF NOT EXISTS facts (
    fact_id       TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,
    text          TEXT NOT NULL,
    source_actor  TEXT NOT NULL,
    evidence      TEXT NOT NULL,
    confidence    REAL NOT NULL DEFAULT 0.7,
    created_at    INTEGER NOT NULL,
    expires_at    INTEGER,
    superseded_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_facts_active ON facts(superseded_by, created_at);

CREATE TABLE IF NOT EXISTS chat_sessions (
    session_id TEXT PRIMARY KEY,
    actor      TEXT NOT NULL,
    started_at INTEGER NOT NULL,
    ended_at   INTEGER,
    turn_count INTEGER NOT NULL DEFAULT 0,
    summary    TEXT
);

CREATE TABLE IF NOT EXISTS chat_turns (
    turn_id    TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    role       TEXT NOT NULL,
    text       TEXT NOT NULL,
    ts         INTEGER NOT NULL,
    refs       TEXT
);
CREATE INDEX IF NOT EXISTS idx_chat_turns_session ON chat_turns(session_id, ts);

-- 参数覆盖：AI 通过提案改的运行时变量（DEFAULTS 之上的一层，可回滚）
CREATE TABLE IF NOT EXISTS params (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  INTEGER NOT NULL,
    actor       TEXT,
    decision_id TEXT,
    expires_at  INTEGER
);

-- 决策日志：谁因为什么改了哪个变量、改前改后、效果如何
CREATE TABLE IF NOT EXISTS decisions (
    decision_id       TEXT PRIMARY KEY,
    ts                INTEGER NOT NULL,
    actor             TEXT NOT NULL,
    capability        TEXT NOT NULL,
    patch             TEXT NOT NULL,
    rationale         TEXT,
    evidence          TEXT,
    before            TEXT,
    after             TEXT,
    applied           INTEGER NOT NULL,
    gate_result       TEXT,
    effect_checked_at INTEGER,
    effect_result     TEXT
);
CREATE INDEX IF NOT EXISTS idx_decisions_ts ON decisions(ts);

-- 推送日志：角色往页面说过什么（也是「AI 能对我说什么」的审计）
CREATE TABLE IF NOT EXISTS push_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      INTEGER NOT NULL,
    kind    TEXT NOT NULL,
    payload TEXT NOT NULL,
    actor   TEXT,
    blocked INTEGER NOT NULL DEFAULT 0,
    reason  TEXT
);
CREATE INDEX IF NOT EXISTS idx_push_log_ts ON push_log(ts);

-- 智能体思考日志：它想了什么、据此做了什么（透明，可审计）
CREATE TABLE IF NOT EXISTS agent_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         INTEGER NOT NULL,
    actor      TEXT NOT NULL,
    stimulus   TEXT,
    thought    TEXT,
    actions    TEXT,
    result     TEXT,
    elapsed_ms INTEGER,
    raw_reply  TEXT
);
CREATE INDEX IF NOT EXISTS idx_agent_log_ts ON agent_log(ts);
"""


def get_db(path) -> sqlite3.Connection:
    """Open (or create) the PAE database. WAL + foreign keys + Row factory."""
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_schema(conn) -> None:
    conn.executescript(DDL)
    conn.commit()


def log_call(conn, actor: str, capability: str, ok: bool, detail: str = "") -> None:
    """记一条能力调用审计（失败不抛，审计不能拖垮主流程）。"""
    try:
        conn.execute(
            "INSERT INTO cap_calls(ts, actor, capability, ok, detail) VALUES(?,?,?,?,?)",
            (_now(), actor, capability, 1 if ok else 0, detail),
        )
        conn.commit()
    except Exception:
        pass


def append_event(conn, type, payload, idem_key=None, source="api", autocommit=True) -> Optional[str]:
    """Append one event. Idempotent when idem_key is given.

    Returns the new event_id, or None when the idem_key already exists.
    autocommit=False: caller is responsible for commit (batch mode).
    """
    event_id = "evt_" + uuid.uuid4().hex[:16]
    ts = _now()
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if idem_key is None:
        conn.execute(
            "INSERT INTO events(event_id, idem_key, ts, type, payload, source)"
            " VALUES(?,?,?,?,?,?)",
            (event_id, None, ts, type, blob, source),
        )
    else:
        cur = conn.execute(
            "INSERT OR IGNORE INTO events(event_id, idem_key, ts, type, payload, source)"
            " VALUES(?,?,?,?,?,?)",
            (event_id, idem_key, ts, type, blob, source),
        )
        if cur.rowcount == 0:
            return None
    if autocommit:
        conn.commit()
    return event_id


def load_events(conn, since_ts=0) -> list:
    """All events with ts >= since_ts, in (ts, rowid) order."""
    rows = conn.execute(
        "SELECT ts, type, payload, idem_key FROM events"
        " WHERE ts >= ? ORDER BY ts ASC, rowid ASC",
        (int(since_ts),),
    ).fetchall()
    out = []
    for r in rows:
        try:
            payload = json.loads(r["payload"])
        except (TypeError, ValueError):
            payload = {}
        out.append({
            "ts": r["ts"],
            "type": r["type"],
            "payload": payload,
            "idem_key": r["idem_key"],
        })
    return out


def lookup_word(conn, word) -> Optional[tuple]:
    """Return (word, translation, exchange, bnc, frq, tag) or None."""
    row = conn.execute(
        "SELECT word, translation, exchange, bnc, frq, tag FROM dict_words WHERE word = ?",
        (str(word).lower(),),
    ).fetchone()
    if row is None:
        return None
    return (row["word"], row["translation"], row["exchange"],
            row["bnc"], row["frq"], row["tag"])


def import_lemma_as_dict(conn, lemma_map_path) -> int:
    """Seed dict_words with every lemma in the lemma map (INSERT OR IGNORE).

    Returns the number of newly inserted rows.
    """
    from .lemmatize import load_lemma_map
    lemma_map = load_lemma_map(lemma_map_path)
    lemmas = sorted(set(lemma_map.values()))
    before = conn.total_changes
    conn.executemany(
        "INSERT OR IGNORE INTO dict_words(word, translation, exchange, bnc, frq, tag)"
        " VALUES(?, NULL, NULL, NULL, NULL, NULL)",
        ((w,) for w in lemmas),
    )
    conn.commit()
    return conn.total_changes - before


def _now() -> int:
    import time
    return int(time.time())
