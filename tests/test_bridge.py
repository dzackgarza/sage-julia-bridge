from __future__ import annotations

import json
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
        # Containers are containers: a Sage vector iterates in, a Julia
        # Vector comes back as a Python list (docs/wire-format.md).
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
        # Julia versions are dotted integers with an optional suffix; a
        # broken worker echoing junk cannot produce parseable components.
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
        # Real discovery failure: HOME and PATH point at an empty
        # directory, so no env var, juliaup install, or PATH hit exists.
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
        # The textual contract: captured stdout precedes the display value.
        self.assertEqual(
            self.bridge.eval('println("side effect"); 42'),
            "side effect\n42",
        )

    def test_error_response_from_julia(self) -> None:
        with self.assertRaises(JuliaError):
            self.bridge.eval('error("deliberate test error")')

    def test_worker_death_mid_request_raises(self) -> None:
        # A worker dying mid-request is the real dead-process boundary:
        # _exit terminates the process instantly, so the read hits EOF on
        # a real pipe. The bridge must fail loudly, then recover with a
        # fresh worker on the next request. (Julia-level exit() is NOT
        # usable here: it closes the pipes but can linger in shutdown,
        # and a half-dead worker defeats the poll()-based restart.)
        with Julia() as bridge:
            with self.assertRaises(JuliaError):
                bridge.eval("ccall(:_exit, Cvoid, (Cint,), 86)")
            self.assertEqual(bridge.eval("1 + 1"), "2")

    def test_protocol_violations_raise(self) -> None:
        # A real subprocess speaking broken protocol over real pipes: the
        # shim answers the startup hello with each malformed frame class
        # (short ok reply, short err reply, unknown status).
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
        # Well-framed reply carrying an unknown value node: the shim
        # answers the startup ping correctly, then serves the bogus node,
        # so the rejection is proved through the public sage() path.
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
        # Real discovery: a real executable on a real PATH, with HOME
        # pointed at a directory that has no juliaup install.
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
            self.assertEqual(bridge.sage("length(HANDLES)"), ZZ(1))
            del handle
            gc.collect()
            self.assertEqual(bridge.sage("length(HANDLES)"), ZZ(0))

    def test_stale_handle_rejected_after_restart(self) -> None:
        # Ids restart from 1 with a new worker; a handle from a previous
        # worker must fail loudly, never silently alias a new object
        # (PR #3 review).
        import gc

        with Julia() as bridge:
            stale = bridge.sage("x -> 10 * x")
            bridge.quit()
            fresh = bridge.sage("x -> 2 * x")  # restarts worker, id 1 again
            with self.assertRaises(JuliaError):
                bridge.call("map", stale, [ZZ(1), ZZ(2)])
            with self.assertRaises(JuliaError):
                stale.sage()
            # A stale handle's GC must not release the new worker's entry.
            del stale
            gc.collect()
            self.assertEqual(bridge.sage("length(HANDLES)"), ZZ(1))
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
