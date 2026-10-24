"""Durable atomic reservations of model attempts (not measured billing).

Legacy successes are frozen once per capability/day at migration. Later audit
successes are not added again. Reserved attempts include failures, in-flight and
unknown outcomes; no automatic refund can permit concurrent overspend.
"""
import time
import uuid
from . import health
from .db_access import access, transaction


class LimitExceeded(ValueError):
    def __init__(self, capability, limit):
        self.payload = {"error": "model_attempt_daily_limit", "capability": capability,
                        "limit": limit, "counting": "reserved_attempts_including_failures_and_unknown",
                        "reset_at": (int(time.time()) // 86400 + 1) * 86400}
        super().__init__("model attempt daily limit")


def reserve(conn, capability, limit):
    day = int(time.time()) // 86400 * 86400
    with access(conn):
        health.require_clean(conn)
        with transaction(conn):
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("CREATE TABLE IF NOT EXISTS model_attempts (attempt_id TEXT PRIMARY KEY, capability TEXT NOT NULL, day INTEGER NOT NULL, ts INTEGER NOT NULL, outcome TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS model_quota_baselines (capability TEXT NOT NULL, day INTEGER NOT NULL, legacy_count INTEGER NOT NULL, PRIMARY KEY(capability,day))")
            baseline = conn.execute("SELECT legacy_count FROM model_quota_baselines WHERE capability=? AND day=?", (capability, day)).fetchone()
            if baseline is None:
                legacy = 0
                if capability == "pae.persona.say":
                    legacy = conn.execute("SELECT COUNT(*) FROM cap_calls WHERE capability=? AND ts>=? AND ts<? AND ok=1", (capability, day, day + 86400)).fetchone()[0]
                elif capability == "pae.sensejudge":
                    legacy = conn.execute("SELECT COUNT(*) FROM sense_shadow_log WHERE ts>=? AND ts<? AND fallback=0", (day, day + 86400)).fetchone()[0]
                conn.execute("INSERT INTO model_quota_baselines VALUES(?,?,?)", (capability, day, legacy))
            else:
                legacy = baseline[0]
            attempts = conn.execute("SELECT COUNT(*) FROM model_attempts WHERE capability=? AND day=?", (capability, day)).fetchone()[0]
            if legacy + attempts >= limit:
                return None
            attempt = uuid.uuid4().hex
            conn.execute("INSERT INTO model_attempts VALUES(?,?,?,?,?)", (attempt, capability, day, int(time.time()), "reserved"))
        return attempt


def finish(conn, attempt, outcome):
    with transaction(conn):
        conn.execute("UPDATE model_attempts SET outcome=? WHERE attempt_id=?", (outcome, attempt))


def usage(conn, capability):
    """Read-only accounting metadata; attempts are not a billing estimate."""
    day = int(time.time()) // 86400 * 86400
    with access(conn):
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('model_attempts','model_quota_baselines')")}
        attempts = 0
        legacy = None
        if "model_attempts" in tables:
            attempts = conn.execute("SELECT COUNT(*) FROM model_attempts WHERE capability=? AND day=?", (capability, day)).fetchone()[0]
        if "model_quota_baselines" in tables:
            row = conn.execute("SELECT legacy_count FROM model_quota_baselines WHERE capability=? AND day=?", (capability, day)).fetchone()
            legacy = row[0] if row else None
        if legacy is None:
            if capability == "pae.persona.say":
                legacy = conn.execute("SELECT COUNT(*) FROM cap_calls WHERE capability=? AND ts>=? AND ts<? AND ok=1", (capability, day, day + 86400)).fetchone()[0]
            elif capability == "pae.sensejudge" and conn.execute("SELECT 1 FROM sqlite_master WHERE name='sense_shadow_log'").fetchone():
                legacy = conn.execute("SELECT COUNT(*) FROM sense_shadow_log WHERE ts>=? AND ts<? AND fallback=0", (day, day + 86400)).fetchone()[0]
            else:
                legacy = 0
        return {"attempts_today": attempts + legacy, "reserved_attempts_today": attempts,
                "legacy_successes_today": legacy, "counting": "attempts_not_billing"}
