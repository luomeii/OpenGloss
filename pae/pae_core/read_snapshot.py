"""Short guarded snapshot acquisition, independently owned read-only WAL scans.

No source commit/rollback and no changes to journal mode. Open outside the
Engine guard; pin after capturing state under that same guard. Close as soon
as SQL inputs have been collected so checkpointing is not held up by models
or Python aggregation. These readers belong to the requesting thread.
"""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .db_access import access
from . import health


class SnapshotUnavailable(RuntimeError):
    pass


class ReadSnapshot:
    def __init__(self, source):
        self.source = source
        self.conn = None
        self.pinned = False
        with access(source):
            databases = source.execute("PRAGMA database_list").fetchall()
            path = next((row[2] for row in databases if row[1] == "main"), "")
            row_factory = source.row_factory
        if not path:
            raise SnapshotUnavailable("read snapshot requires a file database")
        uri = Path(path).resolve().as_uri() + "?mode=ro&cache=private"
        try:
            self.conn = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=0.25)
            self.conn.row_factory = row_factory
            if self.conn.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
                raise SnapshotUnavailable("read snapshot requires WAL mode")
            # Never wait on a database lock while holding the Engine guard.
            self.conn.execute("PRAGMA busy_timeout=0")
        except BaseException:
            self.close()
            raise

    def check_source(self):
        with access(self.source):
            health.check(self.source)
            if self.source.in_transaction:
                raise SnapshotUnavailable("source has an unfinished transaction")

    def pin(self):
        if self.pinned:
            raise SnapshotUnavailable("read snapshot is already pinned")
        with access(self.source):
            self.check_source()
            self.conn.execute("BEGIN")
            # BEGIN alone is deferred: this read establishes the database view.
            self.conn.execute("SELECT rowid FROM events LIMIT 1").fetchone()
            self.pinned = True
        return self.conn

    def close(self):
        conn, self.conn = self.conn, None
        self.pinned = False
        if conn is not None:
            conn.close()


@contextmanager
def read_snapshot(source):
    # Existing raw library connections retain their single-owner contract.
    if not hasattr(source, "access_lock"):
        yield source
        return
    reader = ReadSnapshot(source)
    try:
        yield reader.pin()
        health.check(source)
    finally:
        reader.close()
