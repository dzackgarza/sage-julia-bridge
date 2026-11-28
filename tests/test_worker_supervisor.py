from __future__ import annotations

import fcntl
import select
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class WorkerSupervisorTest(unittest.TestCase):
    def test_launcher_exit_terminates_remaining_group(self) -> None:
        """Preserve launcher status and promptly close descendants' inherited pipes."""
        supervisor = Path(__file__).resolve().parents[1] / "src/sage_julia_bridge/_worker_supervisor.py"
        launcher = """
import subprocess, sys
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(15)'])
print(child.pid, flush=True)
sys.stdin.readline()
raise SystemExit(7)
"""
        with tempfile.TemporaryFile() as owner_lock:
            fcntl.lockf(owner_lock, fcntl.LOCK_EX)
            proc = subprocess.Popen(
                [sys.executable, "-I", str(supervisor), str(owner_lock.fileno()), sys.executable, "-c", launcher],
                pass_fds=(owner_lock.fileno(),),
                process_group=0,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                assert proc.stdout is not None and proc.stdin is not None
                self.assertTrue(select.select([proc.stdout], [], [], 5)[0], "launcher did not report its child")
                self.assertTrue(proc.stdout.readline().strip().isdigit())
                proc.stdin.write("exit\n")
                proc.stdin.flush()
                self.assertEqual(proc.wait(timeout=5), 7)
                self.assertEqual(proc.communicate(timeout=5), ("", ""))
            finally:
                owner_lock.close()
                proc.communicate(timeout=20)
