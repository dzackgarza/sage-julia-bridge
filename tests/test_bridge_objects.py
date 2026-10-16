from __future__ import annotations

import unittest

from sage.all import QQ, ZZ

from sage_julia_bridge import (
    Julia,
    JuliaConversionError,
    JuliaDispatchError,
    JuliaError,
    JuliaReleasedObjectError,
    JuliaStaleObjectError,
    JuliaWorkerError,
    PrimeLocalRings,
    SageOscarRealizationMap,
    batch_ref,
    julia,
    prime_localization,
)
from sage_julia_bridge.interface import BridgeResponse


class Issue11KernelCutoverRedProof(unittest.TestCase):
    """Production-boundary red proof for the issue #11 object-model cutover."""

    bridge: Julia

    @classmethod
    def setUpClass(cls) -> None:
        cls.bridge = Julia()
        cls.bridge.eval(
            """
module Issue11FreshRuntime
export Box, IndexedBox, AffineCallable, box_value, make_affine, same_object, explode_structured

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
make_affine(scale, shift) = value -> scale * value + shift
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
        module = self.bridge.resolve("Issue11FreshRuntime")
        constructor = module.getproperty("AffineCallable")
        callable_object = constructor(ZZ(3), ZZ(5))
        returned_closure = self.bridge.resolve("Issue11FreshRuntime.make_affine")(ZZ(4), ZZ(1))

        self.assertEqual(callable_object(ZZ(4)), ZZ(17))
        self.assertEqual(returned_closure(ZZ(6)), ZZ(25))

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
        self.assertTrue(ZZ(5) in box)
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
            with self.assertRaises(JuliaReleasedObjectError) as released:
                first.sage()
            self.assertEqual(released.exception.kind, "released-object")
            second.release()
            second.release()
            with self.assertRaises(JuliaReleasedObjectError) as again_released:
                second.getproperty("value")
            self.assertEqual(again_released.exception.kind, "released-object")

    def test_conversion_refusal_leaves_foreign_object_usable(self) -> None:
        box = self.bridge.sage("Box(13)")

        with self.assertRaises(JuliaConversionError) as raised:
            box.sage()
        self.assertEqual(raised.exception.kind, "conversion-refused")
        self.assertEqual(raised.exception.backend_type, "Box")
        self.assertEqual(self.bridge.call("box_value", box), ZZ(13))

    def test_target_directed_sage_conversion_registry(self) -> None:
        target = ("issue-11", "box-value")

        self.bridge.conversions.register_to_sage(target, lambda handle: handle.getproperty("value"))
        box = self.bridge.sage("Box(19)")

        self.assertEqual(box.sage(target), ZZ(19))
        with self.assertRaises(JuliaConversionError) as raised:
            box.sage(("issue-11", "unregistered"))
        self.assertEqual(raised.exception.kind, "conversion-refused")
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
        self.assertEqual(raised.exception.backend_type, "DomainError")
        with self.assertRaises(JuliaDispatchError) as dispatch:
            self.bridge.call("sin", box)
        self.assertEqual(dispatch.exception.backend_type, "MethodError")
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
            self.assertEqual(raised.exception.backend_type, "DomainError")
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
            with self.assertRaises(JuliaWorkerError):
                bridge.eval("exit(86)")
            new_pid = bridge.sage("getpid()")

            self.assertNotEqual(new_pid, old_pid)
            with self.assertRaises(JuliaStaleObjectError) as raised:
                stale.getproperty("value")
            self.assertEqual(raised.exception.kind, "stale-object")
            self.assertEqual(bridge.sage("1 + 1"), ZZ(2))

    def test_prime_localization_returns_native_sage_facade_objects(self) -> None:
        from sage.all import PolynomialRing
        from sage.categories.fields import Fields
        from sage.rings.ideal import Ideal_generic
        from sage.rings.morphism import RingHomomorphism

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
        self.assertTrue(iota.oscar().domain().backend_identical(L.oscar().base_ring()))
        self.assertEqual(L.maximal_ideal().oscar().base_ring(), L.oscar())
        self.assertEqual(julia.call("characteristic", residue_field.oscar()), ZZ(0))
        self.assertEqual(rho.oscar().domain(), L.oscar())
        self.assertTrue(local_sum.oscar().backend_equals(iota.oscar()(x + y)))
        self.assertEqual(iota(ZZ(3)), L(ZZ(3)))

        S = PolynomialRing(QQ, ["u", "v"], order="degrevlex")
        with self.assertRaises(JuliaConversionError) as map_raised:
            iota(S.gen(0))
        self.assertEqual(map_raised.exception.kind, "parent-incompatible")
        with self.assertRaises(JuliaConversionError) as constructor_raised:
            L(S.gen(0))
        self.assertEqual(constructor_raised.exception.kind, "parent-incompatible")

    def test_noncoordinate_prime_localization_residue_and_ideal_workflow(self) -> None:
        from sage.all import PolynomialRing

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
        self.assertEqual(iota.oscar().getproperty("domain").sage(), ZZ)
        self.assertEqual(iota.oscar().getproperty("codomain").base_ring().sage(), ZZ)
        self.assertEqual(L.oscar().base_ring().sage(), ZZ)
        self.assertTrue(two.oscar().backend_equals(iota.oscar()(ZZ(2))))

        with self.assertRaises(ZeroDivisionError):
            L(ZZ(1), ZZ(5))
        with self.assertRaises(ZeroDivisionError):
            five.inverse()

    def test_localization_invalid_paths_are_typed_and_leave_worker_usable(self) -> None:
        from sage.all import PolynomialRing

        R = PolynomialRing(QQ, ["x", "y"], order="degrevlex")
        x, y = R.gens()
        with self.assertRaises(ValueError):
            prime_localization(R, R.ideal([x * y]))

        L, iota = prime_localization(R, R.ideal([x]))
        retained = iota(y).oscar()
        retained.release()
        with self.assertRaises(JuliaError) as released:
            julia.call("is_unit", retained)
        self.assertEqual(released.exception.kind, "released-object")

        stale = iota(x).oscar()
        old_pid = julia.sage("getpid()")
        with self.assertRaises(JuliaError):
            julia.eval("exit(86)")
        self.assertNotEqual(julia.sage("getpid()"), old_pid)
        with self.assertRaises(JuliaError) as stale_error:
            julia.call("is_unit", stale)
        self.assertEqual(stale_error.exception.kind, "stale-object")
        self.assertEqual(julia.sage("6 * 7"), ZZ(42))


if __name__ == "__main__":
    unittest.main()
