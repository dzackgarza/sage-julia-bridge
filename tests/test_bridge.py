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
        self.bridge.set("v", vector(ZZ, [1, 2, 3]))
        self.assertEqual(self.bridge.get_sage("v"), [ZZ(1), ZZ(2), ZZ(3)])

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
        self.assertEqual(result, [ZZ(1), ZZ(2)])

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
        self.assertEqual(result, [ZZ(2), ZZ(4), ZZ(6)])

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
            self.assertEqual(result, [ZZ(2), ZZ(4)])

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


class MrdiCodecTest(unittest.TestCase):
    """Parent-aware structured transport via the mrdi subset (issue #1, M3).

    Covers docs/wire-format.md: round trips per tranche-1 constructor,
    homomorphism laws, parent and presentation preservation, recursive
    closure, zero-matrix semantics, and schema-layer rejections.
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

    # -- Julia -> Sage round trips per constructor ---------------------------

    def test_gf_prime_element(self) -> None:
        from sage.all import GF

        result = self.bridge.sage("GF(7)(3)")
        self.assertEqual(result, GF(7)(3))
        self.assertIs(result.parent(), GF(7))

    def test_gf_extension_element_with_explicit_modulus(self) -> None:
        from sage.all import GF

        self.bridge.eval('F49, a = finite_field(7, 2, "a")')
        result = self.bridge.sage("a + 3")
        R = GF(7)["x"]
        x = R.gen()
        expected_field = GF(49, "a", modulus=x**2 + 6 * x + 3)
        self.assertEqual(result, expected_field.gen() + 3)
        # Presentation preservation: the defining modulus is the one Oscar
        # sent, not Sage's default (Conway) choice.
        self.assertEqual(result.parent().modulus(), x**2 + 6 * x + 3)

    def test_zmod_element(self) -> None:
        from sage.all import IntegerModRing

        result = self.bridge.sage("residue_ring(ZZ, 12)[1](7)")
        self.assertEqual(result, IntegerModRing(12)(7))
        self.assertIs(result.parent(), IntegerModRing(12))

    def test_zmod_big_modulus(self) -> None:
        from sage.all import IntegerModRing

        result = self.bridge.sage("residue_ring(ZZ, ZZ(2)^70)[1](ZZ(2)^69 + 5)")
        ring = IntegerModRing(ZZ(2) ** 70)
        self.assertEqual(result, ring(ZZ(2) ** 69 + 5))
        self.assertIs(result.parent(), ring)

    def test_univariate_polynomial(self) -> None:
        from sage.all import PolynomialRing

        result = self.bridge.sage("S, s = polynomial_ring(ZZ, :s); s^4 - 2")
        R = PolynomialRing(ZZ, "s")
        s = R.gen()
        self.assertEqual(result, s**4 - 2)
        self.assertIs(result.parent(), R)

    def test_multivariate_polynomial(self) -> None:
        from sage.all import PolynomialRing

        result = self.bridge.sage(
            "R, (x, y) = polynomial_ring(QQ, [:x, :y]); x^3 - y + 7//2"
        )
        R = PolynomialRing(QQ, ["x", "y"], order="degrevlex")
        x, y = R.gens()
        self.assertEqual(result, x**3 - y + QQ(7) / QQ(2))
        self.assertIs(result.parent(), R)

    def test_variable_order_preserved(self) -> None:
        # Asymmetric fixture: a generator permutation cannot pass.
        result = self.bridge.sage(
            "R, (x, y) = polynomial_ring(QQ, [:x, :y]); x^2 * y"
        )
        R = result.parent()
        self.assertEqual([str(g) for g in R.gens()], ["x", "y"])
        x, y = R.gens()
        self.assertEqual(result, x**2 * y)
        self.assertNotEqual(result, x * y**2)

    def test_matrix_over_gf(self) -> None:
        from sage.all import GF

        result = self.bridge.sage("matrix(GF(7), [1 2; 3 4])")
        self.assertEqual(result, matrix(GF(7), [[1, 2], [3, 4]]))
        self.assertIs(result.base_ring(), GF(7))

    def test_zero_matrix_over_gf(self) -> None:
        # Mandatory: entry-value inference cannot legitimately pass this.
        from sage.all import GF

        result = self.bridge.sage("zero_matrix(GF(7), 2, 3)")
        self.assertTrue(result.is_zero())
        self.assertEqual((result.nrows(), result.ncols()), (2, 3))
        self.assertIs(result.base_ring(), GF(7))

    def test_recursive_closure_matrix_over_mpoly_over_gf(self) -> None:
        from sage.all import GF, PolynomialRing

        result = self.bridge.sage(
            "R, (x, y) = polynomial_ring(GF(7), [:x, :y]); matrix(R, [x y; 0 x + 1])"
        )
        R = PolynomialRing(GF(7), ["x", "y"], order="degrevlex")
        x, y = R.gens()
        self.assertEqual(result, matrix(R, [[x, y], [0, x + 1]]))
        self.assertIs(result.base_ring(), R)

    # -- Parent preservation across one payload ------------------------------

    def test_shared_parent_polynomials(self) -> None:
        self.bridge.eval("Rp, (u, v) = polynomial_ring(QQ, [:u, :v])")
        first = self.bridge.sage("u + v")
        second = self.bridge.sage("u * v")
        self.assertIs(first.parent(), second.parent())
        self.assertEqual(first * second, first.parent()((first * second)))

    def test_shared_parent_finite_field(self) -> None:
        self.bridge.eval('F81, b = finite_field(3, 4, "b")')
        first = self.bridge.sage("b^2 + 1")
        second = self.bridge.sage("b + 2")
        self.assertIs(first.parent(), second.parent())
        self.assertEqual((first * second).parent(), first.parent())

    # -- Sage -> Julia direction and homomorphism laws -----------------------

    def test_homomorphism_laws_through_bridge(self) -> None:
        from sage.all import GF, IntegerModRing, PolynomialRing

        R7 = GF(7)
        Rx = GF(7)["x"]
        K = GF(49, "a", modulus=Rx.gen() ** 2 + 6 * Rx.gen() + 3)
        Z12 = IntegerModRing(12)
        Ps = PolynomialRing(ZZ, "s")
        Pxy = PolynomialRing(QQ, ["x", "y"], order="degrevlex")
        fixtures = [
            (R7(3), R7(5)),
            (K.gen() + 1, K.gen() ** 3),
            (Z12(7), Z12(10)),
            (Ps.gen() ** 2 - 1, Ps.gen() + 3),
            (Pxy.gen(0) + Pxy.gen(1), Pxy.gen(0) - Pxy.gen(1)),
        ]
        for a, b in fixtures:
            with self.subTest(parent=str(a.parent())):
                total = self.bridge.call("+", a, b)
                self.assertEqual(total, a + b)
                # Parent equality, not just coercion equality: ZZ(8) ==
                # Zmod(12)(8) coerces True, so a parent-losing encoder
                # would pass a value-only check.
                self.assertEqual(total.parent(), a.parent())
                product = self.bridge.call("*", a, b)
                self.assertEqual(product, a * b)
                self.assertEqual(product.parent(), a.parent())
                self.assertEqual(self.bridge.call("-", a), -a)
                zero = a.parent()(0)
                one = a.parent()(1)
                self.assertEqual(self.bridge.call("+", a, zero), a)
                self.assertEqual(self.bridge.call("*", a, one), a)

    def test_set_matrix_over_gf_and_det(self) -> None:
        from sage.all import GF

        m = matrix(GF(7), [[1, 2], [3, 4]])
        self.assertEqual(self.bridge.call("det", m), m.det())
        self.assertEqual(self.bridge.call("det", m).parent(), GF(7))

    def test_lex_ring_rejected_on_input(self) -> None:
        from sage.all import PolynomialRing

        R = PolynomialRing(QQ, ["x", "y"], order="lex")
        with self.assertRaises(TypeError):
            self.bridge.set("p", R.gen(0) + R.gen(1))

    # -- Explicit unsupported: handles, not errors or guesses ----------------

    def test_free_module_element_is_handle(self) -> None:
        result = self.bridge.sage("free_module(QQ, 3)([QQ(1), QQ(2), QQ(3)])")
        self.assertIsInstance(result, JuliaHandle)
        with self.assertRaises(TypeError):
            result.sage()

    # -- Schema-layer protocol integrity (decoder unit tests) ----------------

    def test_decoder_rejects_unknown_version(self) -> None:
        from sage_julia_bridge.mrdi import decode_mrdi

        doc = {
            "_ns": {"Oscar": ["https://github.com/oscar-system/Oscar.jl", "9.9.9"]},
            "_type": {"name": "ZZRingElem", "params": {"_type": "ZZRing"}},
            "data": "1",
        }
        with self.assertRaises(JuliaProtocolError):
            decode_mrdi(doc)

    def test_decoder_rejects_non_whitelisted_type(self) -> None:
        from sage_julia_bridge.mrdi import decode_mrdi

        doc = {
            "_ns": {"Oscar": ["https://github.com/oscar-system/Oscar.jl", "1.7.1"]},
            "_type": {"name": "PadicField", "params": {"_type": "PadicField"}},
            "data": "1",
        }
        with self.assertRaises(JuliaProtocolError):
            decode_mrdi(doc)

    def test_decoder_rejects_dangling_ref(self) -> None:
        from sage_julia_bridge.mrdi import decode_mrdi

        doc = {
            "_ns": {"Oscar": ["https://github.com/oscar-system/Oscar.jl", "1.7.1"]},
            "_type": {"name": "FqFieldElem", "params": "not-a-real-ref"},
            "data": "3",
        }
        with self.assertRaises(JuliaProtocolError):
            decode_mrdi(doc)


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
        self.assertEqual(result, [ZZ(1), ZZ(2), ZZ(3)])

    def test_vector_of_qq_elements(self) -> None:
        result = self.bridge.sage("[QQ(1,2), QQ(3,4)]")
        self.assertEqual(result, [QQ(1) / QQ(2), QQ(3) / QQ(4)])

    def test_handle_roundtrip_through_oscar(self) -> None:
        # A free module is codec-uncovered (Oscar cannot serialize it) ->
        # handle; using it as a call argument closes the loop.
        module = self.bridge.sage("free_module(QQ, 2)")
        self.assertIsInstance(module, JuliaHandle)
        self.assertEqual(self.bridge.call("rank", module), ZZ(2))

    def test_ring_as_call_argument(self) -> None:
        # Parents are structured values: Sage ZZ crosses as a ZZRing doc.
        m = matrix(ZZ, [[1, 2], [3, 4]])
        result = self.bridge.call("matrix", ZZ, m)
        self.assertEqual(result, m)
        self.assertIs(result.base_ring(), ZZ)

    def test_toplevel_parent_shares_identity_with_elements(self) -> None:
        # A parent sent as a top-level value must be THE parent of elements
        # sent in later payloads (PR #4 review): the top-level doc carries
        # the same deterministic UUID that element payloads use in _refs.
        from sage.all import PolynomialRing

        R = PolynomialRing(QQ, "s")
        self.bridge.set("Rring", R)
        self.bridge.set("pelem", R.gen() + 1)
        self.assertEqual(self.bridge.eval("parent(pelem) === Rring"), "true")

    def test_parent_objects_decode(self) -> None:
        from sage.all import GF

        self.assertIs(self.bridge.sage("ZZ"), ZZ)
        self.assertIs(self.bridge.sage("GF(7)"), GF(7))

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
