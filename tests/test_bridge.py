from __future__ import annotations

import json
import unittest
from pathlib import Path

from sage.all import QQ, ZZ, matrix, vector

from sage_julia_bridge import (
    Julia,
    JuliaConversionError,
    JuliaError,
    JuliaHandle,
    JuliaProtocolError,
    SageOscarRealizationMap,
    batch_ref,
    julia,
)
from sage_julia_bridge.interface import BridgeResponse


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


class Issue11KernelCutoverRedProof(unittest.TestCase):
    """Production-boundary red proof for the issue #11 object-model cutover."""

    bridge: Julia

    @classmethod
    def setUpClass(cls) -> None:
        cls.bridge = Julia()
        cls.bridge.eval(
            """
module Issue11FreshRuntime
export Box, IndexedBox, AffineCallable, box_value, same_object, explode_structured

mutable struct Box
    value::Int
end

mutable struct IndexedBox
    values::Vector{Int}
end

struct AffineCallable
    scale::Int
    shift::Int
end

(f::AffineCallable)(x) = f.scale * x + f.shift
Base.getindex(box::Box, i::Int) = box.value + i
Base.iterate(box::Box, state=1) = state > 3 ? nothing : (box.value + state, state + 1)
Base.length(box::IndexedBox) = length(box.values)
Base.getindex(box::IndexedBox, i::Int) = box.values[i]
Base.setindex!(box::IndexedBox, value::Int, i::Int) = (box.values[i] = value; box)
Base.iterate(box::IndexedBox, state=1) = state > length(box.values) ? nothing : (box.values[state], state + 1)
Base.:(==)(left::IndexedBox, right::IndexedBox) = left.values == right.values
box_value(box::Box) = box.value
same_object(left, right) = left === right
explode_structured(::Box) = throw(DomainError(:issue11_box, "structured backend failure witness"))
end
using .Issue11FreshRuntime
"""
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.bridge.quit()

    def test_fresh_runtime_callable_object_is_directly_invokable(self) -> None:
        callable_object = self.bridge.sage("AffineCallable(3, 5)")

        self.assertEqual(callable_object(ZZ(4)), ZZ(17))

    def test_fresh_runtime_type_supports_properties_indexing_iteration_and_mutation(self) -> None:
        box = self.bridge.sage("Box(41)")

        self.assertEqual(box.getproperty("value"), ZZ(41))
        box.setproperty("value", ZZ(58))
        self.assertEqual(self.bridge.call("box_value", box), ZZ(58))
        self.assertEqual(box[ZZ(2)], ZZ(60))
        self.assertEqual(list(box), [ZZ(59), ZZ(60), ZZ(61)])

    def test_fresh_runtime_type_supports_length_setindex_equality_and_introspection(self) -> None:
        box, equal_box, distinct_box = self.bridge.sage("begin box = IndexedBox([4, 5, 6]); (box, IndexedBox([4, 5, 6]), IndexedBox([4, 5, 7])) end")

        self.assertEqual(len(box), 3)
        self.assertEqual(box[ZZ(2)], ZZ(5))
        box[ZZ(2)] = ZZ(41)
        self.assertEqual(list(box), [ZZ(4), ZZ(41), ZZ(6)])
        self.assertFalse(box.backend_equals(equal_box))
        self.assertFalse(box == equal_box)
        self.assertFalse(box.backend_identical(equal_box))
        self.assertFalse(box.backend_equals(distinct_box))

        alias = self.bridge.call("identity", box)
        self.assertTrue(box.backend_identical(alias))
        self.assertTrue(box.backend_equals(alias))
        description = box.introspect()
        self.assertEqual(description["julia_type"], "IndexedBox")
        self.assertEqual(description["length_applicable"], True)
        self.assertEqual(description["iteration_applicable"], True)
        self.assertEqual(description["indexing_applicable"], True)
        self.assertGreaterEqual(description["retained_references"], ZZ(2))

    def test_nested_results_preserve_repeated_foreign_reference_identity(self) -> None:
        graph = self.bridge.sage("begin shared = Box(7); Any[shared, [shared, 11], (shared, shared)] end")
        first = graph[0]
        nested = graph[1][0]
        tuple_left, tuple_right = graph[2]

        self.assertEqual(graph[1][1], ZZ(11))
        self.assertEqual(first.identity_key(), nested.identity_key())
        self.assertEqual(tuple_left.identity_key(), tuple_right.identity_key())
        self.assertTrue(self.bridge.call("same_object", first, tuple_left))

    def test_release_reference_accounting_preserves_other_live_proxy(self) -> None:
        with Julia() as bridge:
            bridge.eval(
                """
module Issue11ReleaseRuntime
export Box, box_value

mutable struct Box
    value::Int
end

box_value(box::Box) = box.value
end
using .Issue11ReleaseRuntime
"""
            )
            first, second = bridge.sage("begin shared = Box(97); (shared, shared) end")

            self.assertEqual(first.identity_key(), second.identity_key())
            self.assertEqual(first.introspect()["retained_references"], ZZ(2))
            first.release()
            self.assertEqual(bridge.call("box_value", second), ZZ(97))
            self.assertEqual(second.introspect()["retained_references"], ZZ(1))
            with self.assertRaises(JuliaError) as released:
                first.sage()
            self.assertEqual(getattr(released.exception, "kind"), "released-object")
            second.release()
            second.release()
            with self.assertRaises(JuliaError) as again_released:
                second.getproperty("value")
            self.assertEqual(getattr(again_released.exception, "kind"), "released-object")

    def test_conversion_refusal_leaves_foreign_object_usable(self) -> None:
        box = self.bridge.sage("Box(13)")

        with self.assertRaises(JuliaConversionError) as raised:
            box.sage()
        self.assertEqual(getattr(raised.exception, "kind"), "conversion-refused")
        self.assertEqual(getattr(raised.exception, "backend_type"), "Box")
        self.assertEqual(self.bridge.call("box_value", box), ZZ(13))

    def test_target_directed_sage_conversion_registry(self) -> None:
        target = ("issue-11", "box-value")

        self.bridge.conversions.register_to_sage(target, lambda handle: handle.getproperty("value"))
        box = self.bridge.sage("Box(19)")

        self.assertEqual(box.sage(target), ZZ(19))
        with self.assertRaises(JuliaConversionError) as raised:
            box.sage(("issue-11", "unregistered"))
        self.assertEqual(getattr(raised.exception, "kind"), "conversion-refused")
        self.assertEqual(self.bridge.call("box_value", box), ZZ(19))

    def test_outbound_conversion_registry_is_recursive(self) -> None:
        class RuntimeInt:
            def __init__(self, value: int) -> None:
                self.value = value

        self.bridge.conversions.register_to_julia(
            lambda value: isinstance(value, RuntimeInt),
            lambda value, _encode: {"type": "int", "value": str(value.value)} if isinstance(value, RuntimeInt) else None,
        )

        self.assertEqual(self.bridge.call("+", RuntimeInt(8), ZZ(5)), ZZ(13))
        self.assertEqual(self.bridge.call("sum", [RuntimeInt(2), RuntimeInt(3), ZZ(5)]), ZZ(10))

    def test_backend_failures_are_structured_and_do_not_poison_worker(self) -> None:
        box = self.bridge.sage("Box(23)")

        with self.assertRaises(JuliaError) as raised:
            self.bridge.call("explode_structured", box)
        self.assertEqual(getattr(raised.exception, "backend_type"), "DomainError")
        self.assertEqual(self.bridge.call("box_value", box), ZZ(23))

    def test_structured_batch_preserves_intermediate_identity_in_one_request(self) -> None:
        class CountingJulia(Julia):
            def __init__(self) -> None:
                super().__init__()
                self.operations: list[str] = []

            def _request_unlocked(self, op: str, payload: str) -> BridgeResponse:
                self.operations.append(op)
                return super()._request_unlocked(op, payload)

        bridge = CountingJulia()
        try:
            bridge.eval(
                """
module Issue11BatchRuntime
export Box, box_value, same_object, explode_structured

mutable struct Box
    value::Int
end

box_value(box::Box) = box.value
same_object(left, right) = left === right
explode_structured(::Box) = throw(DomainError(:issue11_box, "structured backend failure witness"))
end
using .Issue11BatchRuntime
"""
            )
            box = bridge.sage("Box(29)")
            bridge.operations.clear()
            first, second, value = bridge.batch(
                [
                    {"op": "call", "bind": "first", "function": "identity", "args": [box]},
                    {"op": "call", "bind": "second", "function": "identity", "args": [batch_ref("first")]},
                    {"op": "call", "bind": "value", "function": "box_value", "args": [batch_ref("second")]},
                    {"op": "result", "value": [batch_ref("first"), batch_ref("second"), batch_ref("value")]},
                ]
            )

            self.assertEqual(bridge.operations, ["batch"])
            self.assertEqual(value, ZZ(29))
            self.assertEqual(first.identity_key(), second.identity_key())
            self.assertTrue(bridge.call("same_object", first, box))

            with self.assertRaises(JuliaError) as raised:
                bridge.batch([{"op": "call", "function": "explode_structured", "args": [box]}])
            self.assertEqual(getattr(raised.exception, "backend_type"), "DomainError")
            self.assertEqual(bridge.call("box_value", box), ZZ(29))
        finally:
            bridge.quit()

    def test_half_dead_worker_exit_restarts_and_stales_old_objects(self) -> None:
        with Julia() as bridge:
            bridge.eval(
                """
module Issue11RestartRuntime
export Box

mutable struct Box
    value::Int
end
end
using .Issue11RestartRuntime
"""
            )
            old_pid = bridge.sage("getpid()")
            stale = bridge.sage("Box(31)")
            with self.assertRaises(JuliaError):
                bridge.eval("exit(86)")
            new_pid = bridge.sage("getpid()")

            self.assertNotEqual(new_pid, old_pid)
            with self.assertRaises(JuliaError) as raised:
                stale.getproperty("value")
            self.assertEqual(getattr(raised.exception, "kind"), "stale-object")
            self.assertEqual(bridge.sage("1 + 1"), ZZ(2))

    def test_prime_localization_returns_native_sage_facade_objects(self) -> None:
        from sage.all import PolynomialRing
        from sage.categories.fields import Fields
        from sage.rings.ideal import Ideal_generic
        from sage.rings.morphism import RingHomomorphism

        import sage_julia_bridge

        prime_localization = getattr(sage_julia_bridge, "prime_localization")
        PrimeLocalRings = getattr(sage_julia_bridge, "PrimeLocalRings")

        R = PolynomialRing(QQ, ["x", "y"], order="degrevlex")
        x, y = R.gens()
        L, iota = prime_localization(R, R.ideal([x]))
        local_element = iota(x)
        local_sum = iota(x + y)
        residue_field, rho = L.residue_field()

        self.assertIsInstance(iota, SageOscarRealizationMap)
        self.assertIsInstance(iota, RingHomomorphism)
        self.assertIsInstance(L.maximal_ideal(), Ideal_generic)
        self.assertIsInstance(rho, RingHomomorphism)
        self.assertIn(L, PrimeLocalRings())
        self.assertIn(residue_field, Fields())
        self.assertIs(iota.domain(), R)
        self.assertIs(iota.codomain(), L)
        self.assertIs(rho.domain(), L)
        self.assertIs(rho.codomain(), residue_field)
        self.assertEqual(rho.kernel(), L.maximal_ideal())
        self.assertTrue(rho.is_surjective())
        self.assertIs(local_element.parent(), L)
        self.assertFalse(local_element.is_unit())
        self.assertTrue(iota(y).is_unit())
        self.assertEqual(L.maximal_ideal(), L.ideal([x]))
        self.assertEqual(rho(local_element), residue_field.zero())
        self.assertEqual(rho(iota(y)), residue_field.gen(1))
        self.assertEqual(iota.oscar().domain(), R)
        self.assertEqual(L.oscar().base_ring(), R)
        self.assertEqual(L.maximal_ideal().oscar().base_ring(), L.oscar())
        self.assertEqual(residue_field.oscar().base_ring(), R)
        self.assertEqual(rho.oscar().domain(), L.oscar())
        self.assertTrue(local_sum.oscar().backend_equals(iota.oscar()(x + y)))
        self.assertEqual(iota(ZZ(3)), L(ZZ(3)))

        S = PolynomialRing(QQ, ["u", "v"], order="degrevlex")
        with self.assertRaises(JuliaConversionError) as map_raised:
            iota(S.gen(0))
        self.assertEqual(getattr(map_raised.exception, "kind"), "parent-incompatible")
        with self.assertRaises(JuliaConversionError) as constructor_raised:
            L(S.gen(0))
        self.assertEqual(getattr(constructor_raised.exception, "kind"), "parent-incompatible")

    def test_noncoordinate_prime_localization_residue_and_ideal_workflow(self) -> None:
        from sage.all import PolynomialRing

        import sage_julia_bridge

        prime_localization = getattr(sage_julia_bridge, "prime_localization")

        R = PolynomialRing(QQ, ["x", "y"], order="degrevlex")
        x, y = R.gens()
        circle = x**2 + y**2 - 1
        prime = R.ideal([circle])
        L, iota = prime_localization(R, prime)
        maximal = L.maximal_ideal()
        residue_field, rho = L.residue_field()
        quotient = R.quotient(prime)

        y_local = iota(y)
        local_fraction = L(x + y, y)

        self.assertTrue(prime.is_prime())
        self.assertTrue(y_local.is_unit())
        self.assertEqual(y_local * y_local.inverse(), L(1))
        self.assertTrue(iota(circle) in maximal)
        self.assertTrue(maximal.is_maximal())
        self.assertIs(maximal.quotient(), residue_field)
        self.assertIs(L.quotient(maximal), residue_field)
        self.assertEqual(L.fraction_field(), R.fraction_field())
        self.assertEqual(local_fraction * y_local, iota(x + y))
        self.assertEqual(rho(iota(circle)), residue_field.zero())
        self.assertEqual(rho(local_fraction), residue_field(quotient(x + y)) / residue_field(quotient(y)))
        self.assertTrue(local_fraction.oscar().backend_equals(iota.oscar()(x + y) / iota.oscar()(y)))
        self.assertEqual((rho * iota)(circle), residue_field.zero())
        self.assertEqual(rho.kernel(), maximal)

        with self.assertRaises(ZeroDivisionError):
            L(x, circle)
        with self.assertRaises(ZeroDivisionError):
            iota(circle).inverse()

    def test_integer_prime_localization_uses_retained_oscar_local_ring(self) -> None:
        import sage_julia_bridge

        prime_localization = getattr(sage_julia_bridge, "prime_localization")

        L, iota = prime_localization(ZZ, ZZ.ideal(5))
        maximal = L.maximal_ideal()
        residue_field, rho = L.residue_field()

        two = iota(ZZ(2))
        five = iota(ZZ(5))

        self.assertIs(two.parent(), L)
        self.assertTrue(two.is_unit())
        self.assertFalse(five.is_unit())
        self.assertEqual(two * two.inverse(), L(1))
        self.assertTrue(five in maximal)
        self.assertFalse(two in maximal)
        self.assertEqual(maximal.gens(), (five,))
        self.assertEqual(rho(two), residue_field(2))
        self.assertEqual(rho(five), residue_field.zero())
        self.assertEqual(residue_field.order(), ZZ(5))
        self.assertEqual(rho.kernel(), maximal)
        self.assertTrue(rho.is_surjective())
        self.assertEqual(iota.oscar().getproperty("domain"), ZZ)
        self.assertEqual(iota.oscar().getproperty("codomain").base_ring(), ZZ)
        self.assertEqual(L.oscar().base_ring(), ZZ)
        self.assertTrue(two.oscar().backend_equals(iota.oscar()(ZZ(2))))

        with self.assertRaises(ZeroDivisionError):
            L(ZZ(1), ZZ(5))
        with self.assertRaises(ZeroDivisionError):
            five.inverse()

    def test_localization_invalid_paths_are_typed_and_leave_worker_usable(self) -> None:
        from sage.all import PolynomialRing

        import sage_julia_bridge

        prime_localization = getattr(sage_julia_bridge, "prime_localization")

        R = PolynomialRing(QQ, ["x", "y"], order="degrevlex")
        x, y = R.gens()
        with self.assertRaises(ValueError):
            prime_localization(R, R.ideal([x * y]))

        L, iota = prime_localization(R, R.ideal([x]))
        retained = iota(y).oscar()
        retained.release()
        with self.assertRaises(JuliaError) as released:
            julia.call("is_unit", retained)
        self.assertEqual(getattr(released.exception, "kind"), "released-object")

        stale = iota(x).oscar()
        old_pid = julia.sage("getpid()")
        with self.assertRaises(JuliaError):
            julia.eval("exit(86)")
        self.assertNotEqual(julia.sage("getpid()"), old_pid)
        with self.assertRaises(JuliaError) as stale_error:
            julia.call("is_unit", stale)
        self.assertEqual(getattr(stale_error.exception, "kind"), "stale-object")
        self.assertEqual(julia.sage("6 * 7"), ZZ(42))


class Issue11GenericOscarSentinelTest(unittest.TestCase):
    """Cross-domain proof that the core object runtime is not ring-shaped."""

    bridge: Julia

    @classmethod
    def setUpClass(cls) -> None:
        cls.bridge = Julia()
        cls.bridge.eval("using Oscar")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.bridge.quit()

    def test_free_module_element_composes_without_codec(self) -> None:
        element, module, doubled, coordinates, exact_parent = self.bridge.sage(
            """
begin
    M = free_module(QQ, 3)
    e = M([QQ(1), QQ(2), QQ(3)])
    (e, parent(e), e + e, coordinates(e), parent(e) === M)
end
"""
        )

        self.assertTrue(exact_parent)
        self.assertEqual(coordinates, [ZZ(1), ZZ(2), ZZ(3)])
        self.assertEqual(self.bridge.call("parent", element).identity_key(), module.identity_key())
        self.assertEqual(self.bridge.call("coordinates", doubled), [ZZ(2), ZZ(4), ZZ(6)])

    def test_matrix_algebra_element_composes_without_codec(self) -> None:
        algebra, element, square, exact_parent = self.bridge.sage(
            """
begin
    A = matrix_algebra(QQ, 2)
    a = A(matrix(QQ, [1 2; 0 1]))
    (A, a, a*a, parent(a) === A)
end
"""
        )

        self.assertTrue(exact_parent)
        self.assertEqual(self.bridge.call("parent", element).identity_key(), algebra.identity_key())
        self.assertNotEqual(square.identity_key(), element.identity_key())

    def test_scheme_and_lattice_inspection_use_same_object_runtime(self) -> None:
        scheme, coordinate_ring, dimension, lattice, gram, rank = self.bridge.sage(
            """
begin
    R, (x, y) = polynomial_ring(QQ, [:x, :y])
    X = spec(R)
    L = quadratic_lattice(QQ; gram=matrix(QQ, [1 0; 0 -1]))
    (X, coordinate_ring(X), dim(X), L, gram_matrix(L), rank(L))
end
"""
        )

        self.assertEqual(dimension, ZZ(2))
        self.assertEqual(self.bridge.call("dim", scheme), ZZ(2))
        self.assertEqual(self.bridge.call("coordinate_ring", scheme), coordinate_ring)
        self.assertEqual(rank, ZZ(2))
        self.assertEqual(gram, matrix(ZZ, [[1, 0], [0, -1]]))
        self.assertEqual(self.bridge.call("gram_matrix", lattice), gram)

    def test_group_and_heterogeneous_graph_preserve_identity(self) -> None:
        module, element, repeated_module, group, generator, repeated_group, group_order = self.bridge.sage(
            """
begin
    M = free_module(QQ, 2)
    e = M([QQ(3), QQ(5)])
    G = symmetric_group(4)
    g = gens(G)[1]
    (M, e, M, G, g, G, order(G))
end
"""
        )

        self.assertEqual(module.identity_key(), repeated_module.identity_key())
        self.assertEqual(group.identity_key(), repeated_group.identity_key())
        self.assertEqual(self.bridge.call("parent", element).identity_key(), module.identity_key())
        self.assertEqual(self.bridge.call("parent", generator).identity_key(), group.identity_key())
        self.assertEqual(group_order, ZZ(24))


class MrdiCodecTest(unittest.TestCase):
    """Parent-aware structured transport via the mrdi subset (issue #1, M3).

    Covers docs/wire-format.md: round trips per tranche-1 constructor,
    homomorphism laws, parent and presentation preservation, recursive
    closure, zero-matrix semantics, and schema-layer rejections.
    """

    bridge: Julia

    @classmethod
    def setUpClass(cls) -> None:
        # Oscar is a hard dependency (provisioned by `just setup`); its
        # absence must fail the suite loudly, not skip it.
        cls.bridge = Julia()
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

        result = self.bridge.sage("R, (x, y) = polynomial_ring(QQ, [:x, :y]); x^3 - y + 7//2")
        R = PolynomialRing(QQ, ["x", "y"], order="degrevlex")
        x, y = R.gens()
        self.assertEqual(result, x**3 - y + QQ(7) / QQ(2))
        self.assertIs(result.parent(), R)

    def test_variable_order_preserved(self) -> None:
        # Asymmetric fixture: a generator permutation cannot pass.
        result = self.bridge.sage("R, (x, y) = polynomial_ring(QQ, [:x, :y]); x^2 * y")
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

        result = self.bridge.sage("R, (x, y) = polynomial_ring(GF(7), [:x, :y]); matrix(R, [x y; 0 x + 1])")
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
        self.assertEqual(first * second, first.parent()(first * second))

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
        # Oscar is a hard dependency (provisioned by `just setup`); its
        # absence must fail the suite loudly, not skip it.
        cls.bridge = Julia()
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

    def test_localization_map_requires_exact_oscar_source_parent(self) -> None:
        # The bridge preserves parent identity for Sage-originating parents,
        # but it must not guess that an independently constructed Oscar ring
        # is "the same" source parent for a retained Oscar map.
        from sage.all import PolynomialRing

        R = PolynomialRing(QQ, ["x", "y"], order="degrevlex")
        x, y = R.gens()

        self.bridge.eval(
            """
Rnative, (xnative, ynative) = polynomial_ring(QQ, [:x, :y])
Pnative = ideal(Rnative, [xnative])
Unative = complement_of_prime_ideal(Pnative, check=false)
RLnative, iotanative = localization(Rnative, Unative)
"""
        )
        self.bridge.set("native_probe_poly", x + y)
        self.assertFalse(self.bridge.sage("parent(native_probe_poly) === Rnative"))
        with self.assertRaises(JuliaError):
            self.bridge.sage("iotanative(native_probe_poly)")

        self.bridge.set("RfromSageProbe", R)
        self.bridge.eval(
            """
xsageProbe, ysageProbe = gens(RfromSageProbe)
PfromSageProbe = ideal(RfromSageProbe, [xsageProbe])
UfromSageProbe = complement_of_prime_ideal(PfromSageProbe, check=false)
RLfromSageProbe, iotafromSageProbe = localization(RfromSageProbe, UfromSageProbe)
"""
        )
        self.bridge.set("sage_parent_probe_poly", x + y)
        self.assertTrue(self.bridge.sage("parent(sage_parent_probe_poly) === RfromSageProbe"))
        self.assertTrue(
            self.bridge.sage(
                """
begin
    value = iotafromSageProbe(sage_parent_probe_poly)
    parent(value) === RLfromSageProbe && value == iotafromSageProbe(xsageProbe + ysageProbe)
end
"""
            )
        )

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
