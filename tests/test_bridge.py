from __future__ import annotations

import sys
import unittest
from importlib import import_module
from pathlib import Path
from unittest.mock import MagicMock, patch

from sage.all import QQ, ZZ, matrix, vector

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

_module = import_module("sage_julia_bridge")
Julia = _module.Julia
JuliaError = _module.JuliaError
JuliaHandle = _module.JuliaHandle
JuliaProtocolError = _module.JuliaProtocolError


class JuliaBridgeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bridge = Julia()

    def tearDown(self) -> None:
        self.bridge.quit()

    def test_repr(self) -> None:
        self.assertEqual(repr(self.bridge), "Julia")

    def test_context_manager(self) -> None:
        with Julia() as bridge:
            self.assertEqual(bridge.eval("1 + 1"), "2")

    def test_eval_and_scalar_coercion(self) -> None:
        self.assertEqual(self.bridge.eval("1 + 1"), "2")
        self.assertEqual(self.bridge.sage("1 + 1"), ZZ(2))
        self.assertEqual(self.bridge.sage("1 // 2"), QQ(1) / QQ(2))

    def test_vector_and_matrix_roundtrip(self) -> None:
        self.bridge.set("v", vector(QQ, [1, QQ(2) / QQ(3), 3]))
        self.assertEqual(self.bridge.get_sage("v"), vector(QQ, [1, QQ(2) / QQ(3), 3]))

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
        version = self.bridge.version()
        self.assertIn(".", version)

    def test_sage_call_alias(self) -> None:
        self.assertEqual(self.bridge("2 * 3"), ZZ(6))

    def test_bool_roundtrip(self) -> None:
        self.assertEqual(self.bridge.sage("true"), True)
        self.assertEqual(self.bridge.sage("false"), False)

    def test_string_roundtrip(self) -> None:
        self.assertEqual(self.bridge.sage('"hello"'), "hello")

    def test_vector_from_integers(self) -> None:
        v = vector(ZZ, [1, 2, 3])
        self.bridge.set("v", v)
        self.assertEqual(self.bridge.get_sage("v"), v)

    def test_matrix_from_integers(self) -> None:
        m = matrix(ZZ, [[1, 2], [3, 4]])
        self.bridge.set("m", m)
        self.assertEqual(self.bridge.get_sage("m"), m)

    def test_list_literal(self) -> None:
        self.bridge.set("lst", [ZZ(1), ZZ(2), ZZ(3)])
        result = self.bridge.get("lst")
        self.assertIn("1", result)

    def test_tuple_literal(self) -> None:
        self.bridge.set("tup", (ZZ(1), ZZ(2)))
        result = self.bridge.get("tup")
        self.assertIn("1", result)

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
        os.environ["SAGE_JULIA_COMMAND"] = self.bridge._command
        try:
            bridge2 = Julia()
            self.assertEqual(bridge2._command, self.bridge._command)
            bridge2.quit()
        finally:
            if orig is None:
                os.environ.pop("SAGE_JULIA_COMMAND", None)
            else:
                os.environ["SAGE_JULIA_COMMAND"] = orig

    def test_julia_not_found_raises(self) -> None:
        import os
        import shutil

        orig_julia = os.environ.get("SAGE_JULIA_COMMAND")
        orig_julia_cmd = os.environ.get("JULIA_COMMAND")
        try:
            os.environ.pop("SAGE_JULIA_COMMAND", None)
            os.environ.pop("JULIA_COMMAND", None)
            original_which = shutil.which
            shutil.which = lambda _: None
            try:
                with patch.object(type(Path.home()), "exists", return_value=False):
                    bridge = Julia.__new__(Julia)
                    with self.assertRaises(JuliaError):
                        bridge._default_command()
            finally:
                shutil.which = original_which
        finally:
            if orig_julia is not None:
                os.environ["SAGE_JULIA_COMMAND"] = orig_julia
            if orig_julia_cmd is not None:
                os.environ["JULIA_COMMAND"] = orig_julia_cmd

    def test_decode_value_nothing(self) -> None:
        bridge = Julia.__new__(Julia)
        self.assertIsNone(bridge._decode_value('{"type": "nothing"}', ""))

    def test_decode_value_bool(self) -> None:
        bridge = Julia.__new__(Julia)
        self.assertTrue(bridge._decode_value('{"type": "bool", "value": true}', ""))
        self.assertFalse(bridge._decode_value('{"type": "bool", "value": false}', ""))

    def test_decode_value_string(self) -> None:
        bridge = Julia.__new__(Julia)
        self.assertEqual(
            bridge._decode_value('{"type": "string", "value": "hello"}', ""), "hello"
        )

    def test_decode_value_int(self) -> None:
        bridge = Julia.__new__(Julia)
        self.assertEqual(
            bridge._decode_value('{"type": "int", "value": "42"}', ""), ZZ(42)
        )

    def test_decode_value_rational(self) -> None:
        bridge = Julia.__new__(Julia)
        result = bridge._decode_value(
            '{"type": "rational", "num": "1", "den": "3"}', ""
        )
        self.assertEqual(result, QQ(1) / QQ(3))

    def test_decode_value_vector(self) -> None:
        bridge = Julia.__new__(Julia)
        data = (
            '{"type": "vector", "data": ['
            '{"type": "int", "value": "1"}, '
            '{"type": "int", "value": "2"}'
            "]}"
        )
        result = bridge._decode_value(data, "")
        self.assertEqual(result, vector(ZZ, [1, 2]))

    def test_decode_value_matrix(self) -> None:
        bridge = Julia.__new__(Julia)
        data = (
            '{"type": "matrix", "nrows": 2, "ncols": 2, "data": ['
            '{"type": "int", "value": "1"}, '
            '{"type": "int", "value": "2"}, '
            '{"type": "int", "value": "3"}, '
            '{"type": "int", "value": "4"}'
            "]}"
        )
        result = bridge._decode_value(data, "")
        self.assertEqual(result, matrix(ZZ, [[1, 2], [3, 4]]))

    def test_decode_value_unsupported(self) -> None:
        bridge = Julia.__new__(Julia)
        data = '{"type": "unsupported", "julia_type": "Function"}'
        with self.assertRaises(TypeError):
            bridge._decode_value(data, "some display")

    def test_decode_value_unknown_type(self) -> None:
        bridge = Julia.__new__(Julia)
        data = '{"type": "bogus"}'
        with self.assertRaises(JuliaProtocolError):
            bridge._decode_value(data, "")

    def test_merge_text(self) -> None:
        bridge = Julia.__new__(Julia)
        result = bridge._merge_text("display", "stdout", "stderr")
        self.assertEqual(result, "stdout\nstderr\ndisplay")

    def test_merge_text_empty(self) -> None:
        bridge = Julia.__new__(Julia)
        result = bridge._merge_text("", "", "")
        self.assertEqual(result, "")

    def test_dead_process_message_no_stderr(self) -> None:
        bridge = Julia.__new__(Julia)
        bridge._stderr = []
        result = bridge._dead_process_message()
        self.assertIn("exited unexpectedly", result)

    def test_dead_process_message_with_stderr(self) -> None:
        bridge = Julia.__new__(Julia)
        bridge._stderr = ["error line 1\n", "error line 2\n"]
        result = bridge._dead_process_message()
        self.assertIn("exited unexpectedly", result)
        self.assertIn("error line 1", result)

    def test_error_response_from_julia(self) -> None:
        with self.assertRaises(JuliaError):
            self.bridge.eval('error("deliberate test error")')

    def test_broken_pipe_error(self) -> None:
        bridge = Julia.__new__(Julia)
        mock_proc = MagicMock()
        bridge._proc = mock_proc
        bridge._stderr = []
        mock_proc.stdin.write.side_effect = BrokenPipeError("broken")
        with self.assertRaises(JuliaError):
            bridge._request_unlocked("ping", "")

    def test_eof_readline(self) -> None:
        bridge = Julia.__new__(Julia)
        mock_proc = MagicMock()
        bridge._proc = mock_proc
        bridge._stderr = []
        mock_proc.stdout.readline.return_value = ""
        with self.assertRaises(JuliaError):
            bridge._request_unlocked("ping", "")

    def test_malformed_ok_response(self) -> None:
        bridge = Julia.__new__(Julia)
        mock_proc = MagicMock()
        bridge._proc = mock_proc
        bridge._stderr = []
        mock_proc.stdout.readline.return_value = "ok\tc2hvcnQ=\n"
        with self.assertRaises(JuliaProtocolError):
            bridge._request_unlocked("ping", "")

    def test_malformed_error_response(self) -> None:
        bridge = Julia.__new__(Julia)
        mock_proc = MagicMock()
        bridge._proc = mock_proc
        bridge._stderr = []
        mock_proc.stdout.readline.return_value = "err\tc2hvcnQ=\n"
        with self.assertRaises(JuliaProtocolError):
            bridge._request_unlocked("ping", "")

    def test_unknown_response_status(self) -> None:
        bridge = Julia.__new__(Julia)
        mock_proc = MagicMock()
        bridge._proc = mock_proc
        bridge._stderr = []
        mock_proc.stdout.readline.return_value = "bogus\tcGF5bG9hZA==\n"
        with self.assertRaises(JuliaProtocolError):
            bridge._request_unlocked("ping", "")

    def test_shutil_which_path(self) -> None:
        import os
        from unittest.mock import patch

        orig_sage = os.environ.get("SAGE_JULIA_COMMAND")
        orig_julia = os.environ.get("JULIA_COMMAND")
        try:
            os.environ.pop("SAGE_JULIA_COMMAND", None)
            os.environ.pop("JULIA_COMMAND", None)
            bridge = Julia.__new__(Julia)
            with patch.object(type(Path.home()), "exists", return_value=False):
                with patch("shutil.which", return_value="/usr/bin/julia"):
                    result = bridge._default_command()
                    self.assertEqual(result, "/usr/bin/julia")
        finally:
            if orig_sage is not None:
                os.environ["SAGE_JULIA_COMMAND"] = orig_sage
            if orig_julia is not None:
                os.environ["JULIA_COMMAND"] = orig_julia

    def test_quit_kill_fallback(self) -> None:
        bridge = Julia.__new__(Julia)
        bridge._lock = MagicMock()
        bridge._lock.__enter__ = MagicMock(return_value=None)
        bridge._lock.__exit__ = MagicMock(return_value=None)
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.stdin.close = MagicMock()
        mock_proc.stdout.close = MagicMock()
        mock_proc.stderr.close = MagicMock()
        mock_proc.terminate.side_effect = Exception("terminate fail")
        mock_proc.kill = MagicMock()
        mock_proc.wait = MagicMock()
        bridge._proc = mock_proc
        bridge._stderr_thread = None
        bridge.quit()
        mock_proc.kill.assert_called()

    def test_oscar_if_installed(self) -> None:
        if self.bridge.eval('Base.find_package("Oscar") === nothing') == "true":
            self.skipTest("Oscar is not installed in Julia")
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
            self.bridge.call('begin global pwned2 = 1; abs end', ZZ(1))
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
        self.assertEqual(result, vector(ZZ, [2, 4, 6]))

    def test_set_handle(self) -> None:
        double = self.bridge.sage("x -> 2 * x")
        self.bridge.set("fn", double)
        self.assertEqual(self.bridge.eval("fn(3)"), "6")

    def test_handle_release_on_gc(self) -> None:
        import gc

        with Julia() as bridge:
            handle = bridge.sage("x -> x")
            self.assertEqual(bridge.eval("length(HANDLES)"), "1")
            del handle
            gc.collect()
            self.assertEqual(bridge.eval("length(HANDLES)"), "0")

    def test_stale_handle_rejected_after_restart(self) -> None:
        # Ids restart from 1 with a new worker; a handle from a previous
        # worker must fail loudly, never silently alias a new object
        # (PR #3 review).
        import gc

        with Julia() as bridge:
            stale = bridge.sage("x -> 10 * x")
            bridge.quit()
            fresh = bridge.sage("x -> 2 * x")  # restarts worker, id 1 again
            with self.assertRaises(AssertionError):
                bridge.call("map", stale, [ZZ(1), ZZ(2)])
            with self.assertRaises(AssertionError):
                stale.sage()
            # A stale handle's GC must not release the new worker's entry.
            del stale
            gc.collect()
            self.assertEqual(bridge.eval("length(HANDLES)"), "1")
            result = bridge.call("map", fresh, [ZZ(1), ZZ(2)])
            self.assertEqual(result, vector(ZZ, [2, 4]))

    def test_float_input_rejected(self) -> None:
        with self.assertRaises(TypeError) as ctx:
            self.bridge.set("f", 1.5)
        self.assertIn("float", str(ctx.exception))
        self.assertIn("eval(", str(ctx.exception))

    def test_dict_input_rejected(self) -> None:
        with self.assertRaises(TypeError) as ctx:
            self.bridge.set("d", {"a": 1})
        self.assertIn("dict", str(ctx.exception))
        self.assertIn("eval(", str(ctx.exception))


class OscarCoercionTest(unittest.TestCase):
    """Structured sage() coercion of Oscar/Nemo values (issue #1, milestone M1).

    Covers the canonical exact conversions: Nemo.ZZRingElem, Nemo.QQFieldElem,
    Nemo.ZZMatrix, Nemo.QQMatrix, and vectors of the scalar types. ZZ/QQ admit
    a unique parent identification, so a parentless wire encoding is exact.
    """

    bridge: Julia

    @classmethod
    def setUpClass(cls) -> None:
        cls.bridge = Julia()
        if cls.bridge.eval('Base.find_package("Oscar") === nothing') == "true":
            cls.bridge.quit()
            raise unittest.SkipTest("Oscar is not installed in Julia")
        cls.bridge.eval("using Oscar")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.bridge.quit()

    def test_zz_scalar(self) -> None:
        result = self.bridge.sage("det(matrix(ZZ,[2 1;1 2]))")
        self.assertEqual(result, ZZ(3))
        self.assertIs(result.parent(), ZZ)

    def test_zz_scalar_exceeds_machine_precision(self) -> None:
        result = self.bridge.sage("factorial(ZZ(30))")
        self.assertEqual(result, ZZ(30).factorial())
        self.assertIs(result.parent(), ZZ)

    def test_qq_scalar(self) -> None:
        result = self.bridge.sage("QQ(1,3)")
        self.assertEqual(result, QQ(1) / QQ(3))
        self.assertIs(result.parent(), QQ)

    def test_zz_matrix(self) -> None:
        result = self.bridge.sage("matrix(ZZ,[1 2;3 4])")
        self.assertEqual(result, matrix(ZZ, [[1, 2], [3, 4]]))
        self.assertIs(result.base_ring(), ZZ)

    def test_qq_matrix(self) -> None:
        result = self.bridge.sage("matrix(QQ,[1 2;3 4])")
        self.assertEqual(result, matrix(QQ, [[1, 2], [3, 4]]))
        self.assertIs(result.base_ring(), QQ)

    def test_qq_matrix_nonintegral_entries(self) -> None:
        result = self.bridge.sage("matrix(QQ,[1//2 2;3 4//3])")
        expected = matrix(QQ, [[QQ(1) / QQ(2), 2], [3, QQ(4) / QQ(3)]])
        self.assertEqual(result, expected)
        self.assertIs(result.base_ring(), QQ)

    def test_vector_of_zz_elements(self) -> None:
        result = self.bridge.sage("[ZZ(1), ZZ(2), ZZ(3)]")
        self.assertEqual(result, vector(ZZ, [1, 2, 3]))

    def test_vector_of_qq_elements(self) -> None:
        result = self.bridge.sage("[QQ(1,2), QQ(3,4)]")
        self.assertEqual(result, vector(QQ, [QQ(1) / QQ(2), QQ(3) / QQ(4)]))

    def test_handle_roundtrip_through_oscar(self) -> None:
        # An Oscar ring is codec-uncovered -> handle; using it as a call
        # argument and coercing the ZZMatrix result back closes the loop.
        zz_ring = self.bridge.sage("ZZ")
        self.assertIsInstance(zz_ring, JuliaHandle)
        m = matrix(ZZ, [[1, 2], [3, 4]])
        result = self.bridge.call("matrix", zz_ring, m)
        self.assertEqual(result, m)
        self.assertIs(result.base_ring(), ZZ)

    def test_qualified_import_oscar(self) -> None:
        # `import Oscar` binds only Main.Oscar — Nemo must be resolved from
        # the value's type, not from a Main binding (PR #2 review).
        with Julia() as bridge:
            bridge.eval("import Oscar")
            result = bridge.sage("Oscar.det(Oscar.matrix(Oscar.ZZ,[2 1;1 2]))")
            self.assertEqual(result, ZZ(3))
            self.assertIs(result.parent(), ZZ)


if __name__ == "__main__":
    unittest.main()
