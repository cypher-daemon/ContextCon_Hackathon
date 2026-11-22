"""Cross-platform single-writer process lock. A second process is refused or waits `lock_wait_s`."""
from __future__ import annotations

import os
import time
from pathlib import Path

if os.name == "nt":
    import msvcrt
else:
    import fcntl

EXIT_LOCK_HELD = 3


class LockHeld(Exception):
    """Another PayPilot process holds the lock."""


class RunLock:
    def __init__(self, path: str | Path, wait_s: float = 0.0):
        self.path, self.wait_s, self._fd = Path(path), wait_s, None

    def acquire(self) -> "RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        deadline = time.monotonic() + self.wait_s
        while True:
            try:
                if os.name == "nt":
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    raise LockHeld(f"another paypilot process holds {self.path}") from None
                time.sleep(0.05)
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, str(os.getpid()).encode())
        self._fd = fd
        return self

    def release(self) -> None:
        if self._fd is not None:          # kernel also drops the lock if we are killed (-9), so no stale locks
            if os.name == "nt":
                os.lseek(self._fd, 0, os.SEEK_SET)
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> "RunLock":
        return self.acquire()

    def __exit__(self, *exc) -> None:
        self.release()
