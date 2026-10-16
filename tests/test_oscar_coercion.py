from __future__ import annotations

import unittest

from sage.all import QQ, ZZ, matrix

from sage_julia_bridge import (
    Julia,
    JuliaError,
    JuliaHandle,
)


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

        integer_ring = self.bridge.sage("ZZ")
        finite_field = self.bridge.sage("GF(7)")

        self.assertIsInstance(integer_ring, JuliaHandle)
        self.assertIsInstance(finite_field, JuliaHandle)
        self.assertIs(integer_ring.sage(), ZZ)
        self.assertIs(finite_field.sage(), GF(7))

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
