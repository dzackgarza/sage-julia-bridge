"""Own a Julia worker until its original Python client releases its POSIX lock."""

from __future__ import annotations

import errno
import fcntl
import os
import subprocess
import sys


def _owner_released(fd: int) -> bool:
    try:
        fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno not in (errno.EACCES, errno.EAGAIN):
            raise
        return False
    return True


def _supervise(owner_fd: int, owner_group: int, argv: list[str]) -> int:
    if _owner_released(owner_fd):
        return 1
    worker = subprocess.Popen(argv, process_group=owner_group)
    try:
        for fd in (0, 1, 2):
            os.close(fd)
        while not _owner_released(owner_fd):
            try:
                return worker.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                continue
        worker.kill()
        return worker.wait()
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.wait()


if __name__ == "__main__":
    raise SystemExit(_supervise(int(sys.argv[1]), int(sys.argv[2]), sys.argv[3:]))
