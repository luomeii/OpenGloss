"""Process-local quarantine shared by connections to one database instance.

Quarantine is irreversible in this process. No health lock is held during SQL,
models, or waits for connection locks. Restart is the explicit recovery boundary.
"""
from pathlib import Path
import threading


class EngineUnavailable(RuntimeError):
    pass


class Health:
    def __init__(self):
        self.reason = None
        self._lock = threading.RLock()

    def check(self):
        if self.reason is not None:
            raise EngineUnavailable(self.reason)

    def quarantine(self, reason):
        with self._lock:
            if self.reason is None:
                self.reason = str(reason)
        raise EngineUnavailable(self.reason)

    def publish(self, fn, *args, **kwargs):
        # Only short, non-SQL publication. Serializes publication with quarantine.
        with self._lock:
            self.check()
            return fn(*args, **kwargs)


_REGISTRY = {}
_REGISTRY_LOCK = threading.Lock()


def for_path(path):
    if str(path) in (":memory:", ""):
        return Health()  # Private SQLite databases have no shared file identity.
    key = str(Path(path).resolve()).casefold()
    with _REGISTRY_LOCK:
        return _REGISTRY.setdefault(key, Health())


def connection(resource):
    return getattr(resource, "conn", resource)


def check(resource):
    conn = connection(resource)
    gate = getattr(conn, "health", None)
    if gate is not None:
        gate.check()
        if getattr(conn, "_closed", False):
            gate.quarantine("database connection closed; restart required")


def require_clean(resource):
    # Caller owns the connection guard. Never roll back another operation.
    conn = connection(resource)
    check(conn)
    if conn.in_transaction:
        gate = getattr(conn, "health", None)
        if gate is not None:
            gate.quarantine("unfinished transaction without an owner; restart required")
        raise EngineUnavailable("unfinished transaction without an owner")
