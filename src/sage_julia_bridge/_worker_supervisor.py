"""Own a Julia worker until its original Python client releases its POSIX lock."""

from __future__ import annotations

import errno
import fcntl
import os
import select
import signal
import subprocess
import sys
import time
from contextlib import suppress


def _owner_released(fd: int) -> bool:
    try:
        fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno not in (errno.EACCES, errno.EAGAIN):
            raise
        return False
    return True


def _wait_for_exit_or_owner_release(owner_fd: int, worker_pid: int) -> None:
    """Observe exit without releasing the group leader's PID reservation."""
    if sys.platform == "darwin":
        with select.kqueue() as queue:
            event = select.kevent(
                worker_pid,
                filter=select.KQ_FILTER_PROC,
                flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                fflags=select.KQ_NOTE_EXIT,
            )
            try:
                queue.control([event], 0, 0)
            except ProcessLookupError:
                return
            while not _owner_released(owner_fd) and not queue.control(None, 1, 0.1):
                pass
    else:
        while not _owner_released(owner_fd) and os.waitid(os.P_PID, worker_pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None:
            time.sleep(0.1)


def _supervise(owner_fd: int, argv: list[str]) -> int:
    """Own the entire backend group and reap its leader only after group cleanup."""
    if _owner_released(owner_fd):
        return 1
    signal.signal(signal.SIGCHLD, signal.SIG_DFL)
    worker = subprocess.Popen(argv, process_group=0)
    try:
        for fd in (0, 1, 2):
            os.close(fd)
        _wait_for_exit_or_owner_release(owner_fd, worker.pid)
    finally:
        with suppress(ProcessLookupError):
            os.killpg(worker.pid, signal.SIGKILL)
        returncode = worker.wait()
    return returncode


if __name__ == "__main__":
    raise SystemExit(_supervise(int(sys.argv[1]), sys.argv[2:]))
