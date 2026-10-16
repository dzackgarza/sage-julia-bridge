from __future__ import annotations

import unittest

from sage.all import QQ, ZZ, matrix

from sage_julia_bridge import (
    Julia,
    JuliaHandle,
    JuliaProtocolError,
)


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


if __name__ == "__main__":
    unittest.main()
