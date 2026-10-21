"""Connection-owned serialization plus shared instance admission checks."""
import sqlite3
import threading
from contextlib import nullcontext, contextmanager
from functools import wraps
from inspect import signature
from . import health


def access(resource):
    conn = getattr(resource, "conn", resource)
    return getattr(conn, "access_lock", None) or nullcontext()


def guarded(fn, *, _cursor_exhaustion=False):
    resource_name = next(iter(signature(fn).parameters))

    @wraps(fn)
    def call(*args, **kwargs):
        resource = args[0] if args else kwargs.get(resource_name)
        with access(resource):
            health.check(resource)
            try:
                result = fn(*args, **kwargs)
            except Exception as error:
                if _cursor_exhaustion and isinstance(error, StopIteration):
                    raise  # Only native cursor exhaustion is normal control flow.
                conn = health.connection(resource)
                gate = getattr(conn, "health", None)
                if (gate is not None and not getattr(conn, "_closed", False)
                        and conn.in_transaction and not getattr(conn, "_owner_depth", 0)):
                    # A swallowed SQL/helper failure must not leave a transaction
                    # that some later helper can silently commit. Explicit scopes
                    # recover their own failures before this boundary is reached.
                    gate.quarantine("failed operation left an unfinished transaction; restart required")
                raise
            health.check(resource)
            return result
    return call


@contextmanager
def batched_rows(conn, sql, parameters=(), *, batch_size=256):
    """Consume a SELECT in bounded batches under the connection's existing guard.

    The caller consumes rows inside this context. No transaction is committed,
    rolled back, or replaced. Private cursor cleanup also covers early exit and
    SQL/consumer errors. Health checks remain at every fetch and context exit.
    """
    if type(batch_size) is not int or not 1 <= batch_size <= 256:
        raise ValueError("batch_size must be an integer from 1 to 256")
    with access(conn):
        health.check(conn)
        cursor = conn.cursor()
        iterator = None
        try:
            cursor.execute(sql, parameters)

            def rows():
                while True:
                    health.check(conn)
                    batch = cursor.fetchmany(batch_size)
                    health.check(conn)
                    if not batch:
                        return
                    yield from batch

            iterator = rows()
            yield iterator
            health.check(conn)
        finally:
            try:
                if iterator is not None:
                    iterator.close()
            finally:
                cursor.close()


@contextmanager
def transaction(conn):
    """Own a clean transaction, or join an existing explicit managed owner."""
    with access(conn):
        health.check(conn)
        if getattr(conn, "_owner_depth", 0):
            yield
        else:
            health.require_clean(conn)
            with conn:
                yield


class ManagedCursor(sqlite3.Cursor):
    @guarded
    def execute(self, *args, **kwargs):
        return super().execute(*args, **kwargs)

    @guarded
    def executemany(self, *args, **kwargs):
        return super().executemany(*args, **kwargs)

    @guarded
    def executescript(self, *args, **kwargs):
        health.require_clean(self.connection)
        return super().executescript(*args, **kwargs)

    @property
    def conn(self):
        return self.connection

    @guarded
    def fetchone(self):
        return super().fetchone()

    @guarded
    def fetchmany(self, *args, **kwargs):
        return super().fetchmany(*args, **kwargs)

    @guarded
    def fetchall(self):
        return super().fetchall()

    def __next__(self):
        return super().__next__()

    __next__ = guarded(__next__, _cursor_exhaustion=True)

    def close(self):
        with access(self.connection):
            return super().close()


class ManagedConnection(sqlite3.Connection):
    def __init__(self, *args, **kwargs):
        self.access_lock = threading.RLock()
        self._closed = False
        self._owner_depth = 0
        super().__init__(*args, **kwargs)

    @guarded
    def cursor(self, factory=ManagedCursor):
        return super().cursor(factory)

    @guarded
    def execute(self, *args, **kwargs):
        return self.cursor().execute(*args, **kwargs)

    @guarded
    def executemany(self, *args, **kwargs):
        return self.cursor().executemany(*args, **kwargs)

    @guarded
    def executescript(self, *args, **kwargs):
        return self.cursor().executescript(*args, **kwargs)

    @guarded
    def commit(self):
        return super().commit()

    def rollback(self):
        # Cleanup of an owned range must remain possible after quarantine.
        with access(self):
            return super().rollback()

    def close(self):
        with access(self):
            try:
                return super().close()
            finally:
                self._closed = True

    def __enter__(self):
        self.access_lock.acquire()
        try:
            health.check(self)
            result = super().__enter__()
            self._owner_depth += 1
            return result
        except BaseException:
            self.access_lock.release()
            raise

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is not None:
                self.rollback()
            else:
                try:
                    self.commit()
                except BaseException:
                    self.rollback()
                    raise
            if self.in_transaction:
                gate = getattr(self, "health", None)
                if gate is not None:
                    gate.quarantine("connection scope did not finish its transaction; restart required")
            return False
        except BaseException:
            if self.in_transaction:
                gate = getattr(self, "health", None)
                if gate is not None:
                    gate.quarantine("connection scope recovery failed; restart required")
            raise
        finally:
            self._owner_depth -= 1
            self.access_lock.release()
