from __future__ import annotations

import json
import os
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from sage.all import QQ, ZZ, matrix, vector

from sage_julia_bridge import (
    Julia,
    JuliaError,
    JuliaHandle,
    JuliaProtocolError,
)


class JuliaBridgeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bridge = Julia()

    def tearDown(self) -> None:
        self.bridge.quit()

    def test_context_manager(self) -> None:
        with Julia() as bridge:
            self.assertEqual(bridge.eval("1 + 1"), "2")

    def test_eval_and_scalar_coercion(self) -> None:
        self.assertEqual(self.bridge.eval("1 + 1"), "2")
        self.assertEqual(self.bridge.sage("1 + 1"), ZZ(2))
        self.assertEqual(self.bridge.sage("1 // 2"), QQ(1) / QQ(2))

    def test_vector_and_matrix_roundtrip(self) -> None:
        self.bridge.set("v", vector(QQ, [1, QQ(2) / QQ(3), 3]))
        self.assertEqual(self.bridge.get_sage("v"), [1, QQ(2) / QQ(3), 3])

        self.bridge.set("m", matrix(QQ, [[1, QQ(1) / QQ(2)], [QQ(2) / QQ(3), 4]]))
        self.assertEqual(
            self.bridge.get_sage("m"),
            matrix(QQ, [[1, QQ(1) / QQ(2)], [QQ(2) / QQ(3), 4]]),
        )

    def test_set_and_get(self) -> None:
        self.bridge.set("x", ZZ(42))
        self.assertEqual(self.bridge.get("x"), "42")

    def test_call(self) -> None:
        self.bridge.eval("f(a, b) = a + b")
        result = self.bridge.call("f", ZZ(3), ZZ(4))
        self.assertEqual(result, ZZ(7))

    def test_version(self) -> None:
        major, minor = self.bridge.version().split(".")[:2]
        self.assertGreaterEqual(int(major), 1)
        self.assertGreaterEqual(int(minor), 0)

    def test_protocol_negotiation_reports_version_and_capabilities(self) -> None:
        capabilities = self.bridge.capabilities()

        self.assertEqual(self.bridge.protocol_version(), 1)
        self.assertIn("runtime.retained-objects", capabilities)
        self.assertIn("runtime.batch", capabilities)
        self.assertIn("transport.structured-errors", capabilities)

    def test_sage_call_alias(self) -> None:
        self.assertEqual(self.bridge("2 * 3"), ZZ(6))

    def test_bool_roundtrip(self) -> None:
        self.assertEqual(self.bridge.sage("true"), True)
        self.assertEqual(self.bridge.sage("false"), False)

    def test_string_roundtrip(self) -> None:
        self.assertEqual(self.bridge.sage('"hello"'), "hello")

    def test_vector_from_integers(self) -> None:
        self.bridge.set("v", vector(ZZ, [1, 2, 3]))
        self.assertEqual(self.bridge.get_sage("v"), [ZZ(1), ZZ(2), ZZ(3)])

    def test_matrix_from_integers(self) -> None:
        m = matrix(ZZ, [[1, 2], [3, 4]])
        self.bridge.set("m", m)
        self.assertEqual(self.bridge.get_sage("m"), m)

    def test_list_roundtrip(self) -> None:
        self.bridge.set("lst", [ZZ(1), ZZ(2), ZZ(3)])
        self.assertEqual(self.bridge.get_sage("lst"), [ZZ(1), ZZ(2), ZZ(3)])

    def test_tuple_becomes_container(self) -> None:
        self.bridge.set("tup", (ZZ(1), ZZ(2)))
        self.assertEqual(self.bridge.get_sage("tup"), [ZZ(1), ZZ(2)])

    def test_bool_false(self) -> None:
        self.bridge.set("b", False)
        self.assertEqual(self.bridge.get("b"), "false")

    def test_nothing_roundtrip(self) -> None:
        self.assertIsNone(self.bridge.sage("nothing"))

    def test_unsupported_type_raises(self) -> None:
        with self.assertRaises(TypeError):
            self.bridge.set("bad", object())

    def test_env_var_command(self) -> None:
        import os

        orig = os.environ.get("SAGE_JULIA_COMMAND")
        os.environ["SAGE_JULIA_COMMAND"] = "/custom/julia --threads=2"
        try:
            bridge2 = Julia()
            self.assertEqual(bridge2._command, "/custom/julia --threads=2")
        finally:
            if orig is None:
                os.environ.pop("SAGE_JULIA_COMMAND", None)
            else:
                os.environ["SAGE_JULIA_COMMAND"] = orig

    def test_julia_not_found_raises(self) -> None:
        import os
        import tempfile

        orig = {name: os.environ.get(name) for name in ("SAGE_JULIA_COMMAND", "JULIA_COMMAND", "HOME", "PATH")}
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ.pop("SAGE_JULIA_COMMAND", None)
                os.environ.pop("JULIA_COMMAND", None)
                os.environ["HOME"] = tmp
                os.environ["PATH"] = tmp
                bridge = Julia.__new__(Julia)
                with self.assertRaises(JuliaError):
                    bridge._default_command()
        finally:
            for name, value in orig.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    def test_eval_merges_printed_output_before_display(self) -> None:
        self.assertEqual(
            self.bridge.eval('println("side effect"); 42'),
            "side effect\n42",
        )

    def test_error_response_from_julia(self) -> None:
        with self.assertRaises(JuliaError):
            self.bridge.eval('error("deliberate test error")')

    def test_worker_death_mid_request_raises(self) -> None:
        """Abrupt process exit must fail the request and permit a fresh session; Julia-level exit can linger during shutdown."""
        with Julia() as bridge:
            with self.assertRaises(JuliaError):
                bridge.eval("ccall(:_exit, Cvoid, (Cint,), 86)")
            self.assertEqual(bridge.eval("1 + 1"), "2")

    @unittest.skipUnless(sys.platform == "linux", "Linux process identity check uses pidfds and /proc")
    def test_worker_dies_with_owner(self) -> None:
        self._assert_worker_dies_with_owner(None)

    @unittest.skipUnless(sys.platform == "linux", "Linux process identity check uses pidfds and /proc")
    def test_launcher_worker_dies_with_owner(self) -> None:
        self._assert_worker_dies_with_owner("sh -c 'julia \"$@\"; status=$?; exit \"$status\"' sh")

    def _assert_worker_dies_with_owner(self, command: str | None) -> None:
        owner_code = """
import json, subprocess, sys, threading, time
import sage.all
from sage_julia_bridge import Julia

bridge = Julia(command=json.loads(sys.argv[3]))
worker_pids = []
starter = threading.Thread(target=lambda: worker_pids.append(int(bridge.eval('getpid()'))))
starter.start()
starter.join()
worker_pid, = worker_pids
sibling = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print(json.dumps([worker_pid, sibling.pid]), flush=True)
if sys.argv[1] == 'busy':
    marker = json.dumps(sys.argv[2])
    bridge.eval(f'write({marker}, "started"); while true; sleep(1); end')
else:
    time.sleep(600)
"""
        for state in ("idle", "busy"):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as directory:
                marker = Path(directory) / "started"
                owner = subprocess.Popen(
                    [sys.executable, "-u", "-c", owner_code, state, str(marker), json.dumps(command)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    start_new_session=True,
                )
                backend_fds: dict[int, int] = {}
                sibling_fd = None
                try:
                    assert owner.stdout is not None
                    self.assertTrue(select.select([owner.stdout], [], [], 90)[0], "owner did not report its Julia PID")
                    worker_pid, sibling_pid = json.loads(owner.stdout.readline())
                    sibling_fd = os.pidfd_open(sibling_pid)
                    self.assertEqual(os.getpgid(sibling_pid), owner.pid)
                    pid = worker_pid
                    while pid != owner.pid:
                        self.assertGreater(pid, 1, "Julia is not descended from its owner")
                        self.assertNotIn(pid, backend_fds, "cycle in backend ancestry")
                        backend_fds[pid] = os.pidfd_open(pid)
                        status = Path(f"/proc/{pid}/status").read_text()
                        pid = int(status.split("PPid:\t", 1)[1].splitlines()[0])
                    if command is not None:
                        self.assertGreaterEqual(len(backend_fds), 3, "expected real launcher and supervisor ancestors")
                    if state == "busy":
                        deadline = time.monotonic() + 30
                        while (not marker.exists() or not marker.stat().st_size) and time.monotonic() < deadline:
                            time.sleep(0.05)
                        self.assertEqual(marker.read_text(), "started")
                    worker_exit = select.poll()
                    worker_exit.register(backend_fds[worker_pid], select.POLLIN)
                    self.assertEqual(worker_exit.poll(0), [])
                    owner.kill()
                    self.assertEqual(owner.wait(timeout=10), -signal.SIGKILL)
                    self.assertTrue(worker_exit.poll(5000), f"{state} Julia worker {worker_pid} survived its owner")
                    for pid, fd in backend_fds.items():
                        exited = select.poll()
                        exited.register(fd, select.POLLIN)
                        self.assertTrue(exited.poll(5000), f"backend ancestor {pid} survived its owner")
                    sibling_exit = select.poll()
                    sibling_exit.register(sibling_fd, select.POLLIN)
                    self.assertEqual(sibling_exit.poll(0), [], "unrelated process in owner group was killed")
                finally:
                    if owner.poll() is None:
                        if sibling_fd is None:
                            os.killpg(owner.pid, signal.SIGKILL)
                        else:
                            owner.kill()
                        owner.wait(timeout=10)
                    for fd in [*backend_fds.values(), *([sibling_fd] if sibling_fd is not None else [])]:
                        try:
                            signal.pidfd_send_signal(fd, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        finally:
                            os.close(fd)
                    owner.communicate(timeout=10)
        with Julia(command=command) as fresh:
            self.assertEqual(fresh.eval("1 + 1"), "2")

    def test_protocol_violations_raise(self) -> None:
        """Exercise malformed startup responses over actual subprocess pipes."""
        import shlex
        import sys
        import tempfile

        for reply in ("ok\tc2hvcnQ=", "err\tc2hvcnQ=", "bogus\tcGF5bG9hZA=="):
            with self.subTest(reply=reply):
                with tempfile.TemporaryDirectory() as tmp:
                    shim = Path(tmp) / "shim.py"
                    shim.write_text(f"import sys\nsys.stdin.readline()\nsys.stdout.write({reply!r} + '\\n')\nsys.stdout.flush()\n")
                    bridge = Julia(command=f"{shlex.quote(sys.executable)} {shlex.quote(str(shim))}")
                    with self.assertRaises(JuliaProtocolError):
                        bridge.eval("1 + 1")
                    bridge.quit()

    def test_unknown_structured_node_raises(self) -> None:
        import base64
        import shlex
        import sys
        import tempfile

        def b64(text: str) -> str:
            return base64.b64encode(text.encode()).decode()

        hello_text = '{"protocol_version":1,"capabilities":["runtime.retained-objects"]}'
        hello_node = b64(json.dumps({"type": "string", "value": hello_text}))
        bogus_node = b64('{"type":"bogus"}')
        ok_hello = f"ok\t{b64(hello_text)}\t{hello_node}\t\t"
        ok_bogus = f"ok\t\t{bogus_node}\t\t"
        with tempfile.TemporaryDirectory() as tmp:
            shim = Path(tmp) / "shim.py"
            shim.write_text(
                "import sys\n"
                "for reply in (" + repr(ok_hello) + ", " + repr(ok_bogus) + "):\n"
                "    sys.stdin.readline()\n"
                "    sys.stdout.write(reply + '\\n')\n"
                "    sys.stdout.flush()\n"
            )
            bridge = Julia(command=f"{shlex.quote(sys.executable)} {shlex.quote(str(shim))}")
            with self.assertRaises(JuliaProtocolError):
                bridge.sage("1 + 1")
            bridge.quit()

    def test_default_command_from_path(self) -> None:
        import os
        import tempfile

        orig = {name: os.environ.get(name) for name in ("SAGE_JULIA_COMMAND", "JULIA_COMMAND", "HOME", "PATH")}
        try:
            with tempfile.TemporaryDirectory() as tmp:
                fake = Path(tmp) / "julia"
                fake.write_text("#!/bin/sh\nexit 0\n")
                fake.chmod(0o755)
                os.environ.pop("SAGE_JULIA_COMMAND", None)
                os.environ.pop("JULIA_COMMAND", None)
                os.environ["HOME"] = tmp
                os.environ["PATH"] = tmp
                bridge = Julia.__new__(Julia)
                self.assertEqual(bridge._default_command(), str(fake))
        finally:
            for name, value in orig.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    def test_worker_names_do_not_shadow_user_exports(self) -> None:
        names = (
            "json_escape", "capture", "reply", "b64", "display_text", "resolve_path",
            "encode_value", "decode_value", "bridge_evaluate", "JSON", "Base64",
            "HANDLES", "BRIDGE_CAPABILITIES", "BridgeCoercionMap", "BridgeLocalizedIdeal",
            "bridge_localized_ideal", "bridge_fraction_residue_map", "SageJuliaBridgeKernel",
        )
        exports = ", ".join(names)
        definitions = "; ".join(f"{name}() = {i}" for i, name in enumerate(names, 1))
        self.bridge.eval(f"module Probe; export {exports}; {definitions}; end")
        self.assertEqual(self.bridge.eval("using .Probe"), "")
        self.assertEqual(
            self.bridge.sage("(" + ", ".join(f"{name}()" for name in names) + ")"),
            list(range(1, len(names) + 1)),
        )
        self.assertEqual(self.bridge.call("capture"), 2)
        self.assertEqual(self.bridge.resolve("reply")(), 3)

    def test_conflicts_between_user_modules_retain_julia_semantics(self) -> None:
        self.bridge.eval("module A; export probe; probe() = 1; end")
        self.bridge.eval("module B; export probe; probe() = 2; end")
        self.bridge.eval("using .A, .B")
        with self.assertRaises(JuliaError):
            self.bridge.sage("probe()")
        self.assertEqual(self.bridge.sage("(A.probe(), B.probe())"), [1, 2])

    def test_oscar_loads(self) -> None:
        self.assertEqual(self.bridge.eval("using Oscar"), "")
        oscar_code = """begin
using Oscar
R, (x, y) = QQ[:x, :y]
string(x^2 + y^2)
end"""
        self.assertEqual(self.bridge.eval(oscar_code), "x^2 + y^2")


class ProtocolTest(unittest.TestCase):
    """set/call as protocol operations with structured values (issue #1, M2).

    Values travel as data, never as interpolated Julia source; values the
    codec does not cover come back as opaque JuliaHandle references.
    """

    bridge: Julia

    @classmethod
    def setUpClass(cls) -> None:
        cls.bridge = Julia()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.bridge.quit()

    def test_set_string_roundtrip(self) -> None:
        self.bridge.set("s", "hello world")
        self.assertEqual(self.bridge.get_sage("s"), "hello world")

    def test_set_string_with_julia_syntax_is_data(self) -> None:
        hostile = 'x"); global pwned = 1; error("boom"); ("\n\t;'
        self.bridge.set("s", hostile)
        self.assertEqual(self.bridge.get_sage("s"), hostile)
        self.assertEqual(self.bridge.eval("isdefined(Main, :pwned)"), "false")

    def test_set_none(self) -> None:
        self.bridge.set("n", None)
        self.assertIsNone(self.bridge.get_sage("n"))

    def test_call_with_string_args(self) -> None:
        self.bridge.eval('shout(s) = s * "!"')
        self.assertEqual(self.bridge.call("shout", "abc"), "abc!")

    def test_call_with_kwargs(self) -> None:
        self.bridge.eval("addk(x; delta=0) = x + delta")
        self.assertEqual(self.bridge.call("addk", ZZ(3), delta=ZZ(4)), ZZ(7))

    def test_call_dotted_path(self) -> None:
        self.assertEqual(self.bridge.call("Base.abs", ZZ(-5)), ZZ(5))

    def test_call_path_is_not_executed(self) -> None:
        with self.assertRaises(JuliaError):
            self.bridge.call("begin global pwned2 = 1; abs end", ZZ(1))
        self.assertEqual(self.bridge.eval("isdefined(Main, :pwned2)"), "false")

    def test_unsupported_value_returns_handle(self) -> None:
        result = self.bridge.sage("x -> x + 1")
        self.assertIsInstance(result, JuliaHandle)

    def test_handle_materialize_unsupported_raises(self) -> None:
        handle = self.bridge.sage("x -> x + 1")
        with self.assertRaises(TypeError):
            handle.sage()

    def test_handle_as_call_argument(self) -> None:
        double = self.bridge.sage("x -> 2 * x")
        result = self.bridge.call("map", double, [ZZ(1), ZZ(2), ZZ(3)])
        self.assertEqual(result, [ZZ(2), ZZ(4), ZZ(6)])

    def test_set_handle(self) -> None:
        double = self.bridge.sage("x -> 2 * x")
        self.bridge.set("fn", double)
        self.assertEqual(self.bridge.eval("fn(3)"), "6")

    def test_handle_release_on_gc(self) -> None:
        import gc

        with Julia() as bridge:
            handle = bridge.sage("x -> x")
            handle_id = handle.identity_key()[2]
            self.assertEqual(handle.introspect()["retained_references"], ZZ(1))
            del handle
            gc.collect()
            with self.assertRaises(JuliaError) as raised:
                bridge._request("object", json.dumps({"op": "introspect", "id": handle_id}))
            self.assertEqual(raised.exception.kind, "backend")
            self.assertEqual(raised.exception.backend_type, "ErrorException")
            self.assertEqual(bridge.sage("6 * 7"), ZZ(42))

    def test_stale_handle_rejected_after_restart(self) -> None:
        """Reused IDs must not alias stale objects, including on garbage collection."""
        import gc

        with Julia() as bridge:
            stale = bridge.sage("x -> 10 * x")
            stale_id = stale.identity_key()[2]
            bridge.quit()
            fresh = bridge.sage("x -> 2 * x")
            self.assertEqual(fresh.identity_key()[2], stale_id)
            with self.assertRaises(JuliaError):
                bridge.call("map", stale, [ZZ(1), ZZ(2)])
            with self.assertRaises(JuliaError):
                stale.sage()
            del stale
            gc.collect()
            self.assertEqual(fresh.introspect()["retained_references"], ZZ(1))
            result = bridge.call("map", fresh, [ZZ(1), ZZ(2)])
            self.assertEqual(result, [ZZ(2), ZZ(4)])

    def test_float_input_rejected(self) -> None:
        with self.assertRaises(TypeError):
            self.bridge.set("f", 1.5)

    def test_dict_input_rejected(self) -> None:
        with self.assertRaises(TypeError):
            self.bridge.set("d", {"a": 1})


if __name__ == "__main__":
    unittest.main()
