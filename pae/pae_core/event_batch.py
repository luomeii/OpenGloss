"""One Engine event batch: stage -> commit/confirm -> publish.

Caller holds the Engine/connection guard for the entire context. The normal
path adds no reader or history replay. Only commit exceptions inspect the IDs
actually inserted by this batch, through a separate read-only connection.
"""
from dataclasses import replace
from pathlib import Path
import sqlite3
from . import health
from .fold import _OBS_ONLY


class EventBatch:
    def __init__(self, engine):
        self.engine = engine
        self.conn = engine.conn
        self.states = {}
        self.seen = set()
        self.ids = []

    def __enter__(self):
        health.require_clean(self.conn)
        self.conn.execute("BEGIN")
        self.conn._owner_depth += 1
        return self

    def fold(self, event_id, event, fold_fn):
        self.ids.append(event_id)
        key = (event["lemma_id"], event.get("sense_id", 0))
        if event["type"] not in _OBS_ONLY and key not in self.states:
            old = self.engine.states.get(key)
            if old is not None:
                self.states[key] = replace(old)
        # Fold is unchanged; only its private destination differs.
        fold_fn(self.states, event, self.engine.params, self.seen)

    def state(self, key):
        return self.states.get(key, self.engine.states.get(key))

    def _fail(self, reason):
        self.conn.health.quarantine(reason + "; restart required")

    def _rollback(self):
        try:
            if self.conn.in_transaction:
                self.conn.rollback()
            if self.conn.in_transaction:
                self._fail("rollback left an unfinished event transaction")
        except health.EngineUnavailable:
            raise
        except BaseException:
            self._fail("event rollback failed")

    def _durable_count(self):
        if not self.ids:
            return 0  # Empty/duplicate batches have no commit evidence to infer.
        uri = Path(self.engine.db_path).resolve().as_uri() + "?mode=ro&cache=private"
        reader = None
        try:
            reader = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=0)
            # Primary-key lookups only; one read transaction fixes their view.
            reader.execute("BEGIN")
            return sum(reader.execute("SELECT 1 FROM events WHERE event_id=?", (eid,)).fetchone() is not None
                       for eid in self.ids)
        except BaseException:
            self._fail("could not confirm event commit result")
        finally:
            if reader is not None:
                reader.close()

    def _publish(self):
        try:
            self.engine.states.update(self.states)
            self.engine.seen_idem.update(self.seen)
        except BaseException:
            self._fail("committed events could not be published to memory")

    def __exit__(self, exc_type, exc, tb):
        try:
            return self._finish(exc_type, exc, tb)
        finally:
            self.conn._owner_depth -= 1

    def _finish(self, exc_type, exc, tb):
        if exc_type is not None:
            self._rollback()
            # SQL/fold errors happen before this scope attempts a commit.
            # Explicit BEGIN prevents accidental autocommit of earlier inserts.
            return False
        try:
            self.conn.commit()
        except BaseException:
            active = self.conn.in_transaction
            self._rollback()
            if active:
                raise  # Owned rollback proves the failed transaction did not commit.
            durable = self._durable_count()
            if not self.ids or durable == 0:
                raise  # Confirmed unsuccessful batch; caller may retry normally.
            if durable != len(self.ids):
                self._fail("partial durable event batch")
            # Commit happened despite an exception: publish exactly once.
        self.conn.health.publish(self._publish)
        return False
