"""fsutil.py — the engine's safe file writes: whole-file replace and an advisory lock. Stdlib only (POSIX `fcntl`;
where it is missing the lock is a no-op and the write stays atomic)."""
from __future__ import annotations
import contextlib
import os
import tempfile

try:
    import fcntl
except ImportError:  # pragma: no cover — non-POSIX host
    fcntl = None  # type: ignore[assignment]


def atomic_write(path: str, text: str) -> None:
    """Write `text` to `path` so a reader sees the old file or the new one, never a torn one: a temp file in the
    same directory, fsync'd, then `os.replace`. The temp file is removed when anything fails."""
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=f".{os.path.basename(path)}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


@contextlib.contextmanager
def locked(path: str):
    """Hold an exclusive advisory lock on `<path>.lock` for a read-modify-write of `path` (session + heartbeat
    write the same file). Blocks until the other writer is done."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(f"{path}.lock", "a", encoding="utf-8") as lk:
        if fcntl is not None:
            fcntl.flock(lk.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lk.fileno(), fcntl.LOCK_UN)
