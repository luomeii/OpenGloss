"""Cross-process ownership of this installation's data, including startup/shutdown."""
from contextlib import contextmanager
from pathlib import Path
@contextmanager
def data_owner(root):
    import msvcrt
    with (Path(root)/'data-owner.lock').open('a+b') as stream:
        if stream.tell()==0:stream.write(b'0');stream.flush()
        stream.seek(0)
        try:msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:raise RuntimeError('This installation data is still owned by a running process; retry after it exits')
        try:yield
        finally:stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
