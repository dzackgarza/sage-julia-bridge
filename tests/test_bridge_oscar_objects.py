from __future__ import annotations

import unittest

from sage.all import ZZ

from sage_julia_bridge import (
    Julia,
)


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
        module, submodule, inclusion, quotient, projection, kernel, kernel_inclusion, relation = self.bridge.sage(
            """
begin
    M = free_module(QQ, 3)
    e1, e2, e3 = gens(M)
    S, inc = sub(M, [e1 + 2*e2])
    Q, proj = quo(M, S)
    K, kinc = kernel(proj)
    (M, S, inc, Q, proj, K, kinc, e1 + 2*e2)
end
"""
        )

        self.assertEqual(inclusion.domain().identity_key(), submodule.identity_key())
        self.assertEqual(inclusion.codomain().identity_key(), module.identity_key())
        self.assertEqual(projection.domain().identity_key(), module.identity_key())
        self.assertEqual(projection.codomain().identity_key(), quotient.identity_key())
        self.assertEqual(kernel_inclusion.domain().identity_key(), kernel.identity_key())
        self.assertEqual(kernel_inclusion.codomain().identity_key(), module.identity_key())
        self.assertTrue(self.bridge.call("is_zero", projection(relation)))
        submodule_generator = self.bridge.call("gen", submodule, ZZ(1))
        kernel_generator = self.bridge.call("gen", kernel, ZZ(1))
        self.assertTrue(inclusion(submodule_generator).backend_equals(kernel_inclusion(kernel_generator)))

    def test_matrix_algebra_element_composes_without_codec(self) -> None:
        algebra, center, center_inclusion, central_element, product, center_dimension = self.bridge.sage(
            """
begin
    A = matrix_algebra(QQ, 2)
    C, CtoA = center(A)
    c = basis(C)[1]
    a = A(matrix(QQ, [1 2; 3 5]))
    (A, C, CtoA, CtoA(c), a*a, dimension_of_center(A))
end
"""
        )

        self.assertEqual(center_dimension, ZZ(1))
        self.assertEqual(center_inclusion.domain().identity_key(), center.identity_key())
        self.assertEqual(center_inclusion.codomain().identity_key(), algebra.identity_key())
        self.assertEqual(self.bridge.call("parent", central_element).identity_key(), algebra.identity_key())
        self.assertEqual(self.bridge.call("parent", product).identity_key(), algebra.identity_key())
        self.assertTrue(self.bridge.call("is_central", algebra))

    def test_affine_scheme_subobject_and_morphism_preserve_graph(self) -> None:
        scheme, coordinate_ring, closed, closed_ring, inclusion, pullback, defining_kernel, identity_map = self.bridge.sage(
            """
begin
    X = affine_space(QQ, 3)
    R = OO(X)
    x1, x2, x3 = gens(R)
    Y = subscheme(X, x1*x2 - x3)
    S = OO(Y)
    inc = inclusion_morphism(Y, X)
    pb = pullback(inc)
    K = kernel(pb)
    (X, R, Y, S, inc, pb, K, identity_map(X))
end
"""
        )

        self.assertEqual(inclusion.domain().identity_key(), closed.identity_key())
        self.assertEqual(inclusion.codomain().identity_key(), scheme.identity_key())
        self.assertEqual(pullback.domain().identity_key(), coordinate_ring.identity_key())
        self.assertEqual(pullback.codomain().identity_key(), closed_ring.identity_key())
        self.assertEqual(self.bridge.call("base_ring", defining_kernel).identity_key(), pullback.domain().identity_key())
        self.assertTrue(self.bridge.call("compose", inclusion, identity_map).backend_equals(inclusion))

    def test_indefinite_lattice_ambient_space_and_invariants(self) -> None:
        lattice, ambient, gram, scaled, scaled_ambient, determinant, signature = self.bridge.sage(
            """
begin
    G = matrix(QQ, [1 0 0; 0 -1 0; 0 0 2])
    L = quadratic_lattice(QQ; gram=G)
    M = 2*L
    (L, ambient_space(L), gram_matrix(L), M, ambient_space(M), det(gram_matrix(L)), signature_tuple(L))
end
"""
        )

        self.assertEqual(determinant, ZZ(-2))
        self.assertEqual(signature, [ZZ(2), ZZ(0), ZZ(1)])
        self.assertEqual(self.bridge.call("rank", lattice), ZZ(3))
        self.assertEqual(self.bridge.call("ambient_space", lattice).identity_key(), ambient.identity_key())
        self.assertEqual(self.bridge.call("ambient_space", scaled).identity_key(), scaled_ambient.identity_key())
        self.assertTrue(self.bridge.call("is_integral", scaled))
        self.assertEqual(self.bridge.call("det", gram), ZZ(-2))

    def test_group_subgroup_homomorphism_and_heterogeneous_graph(self) -> None:
        group, subgroup, embedding, homomorphism, image, image_embedding, module, projection, repeated = self.bridge.sage(
            """
begin
    G = symmetric_group(4)
    H, emb = derived_subgroup(G)
    phi = id_hom(G)
    I, iemb = image(phi)
    M = free_module(QQ, 2)
    S, _ = sub(M, [gen(M, 1)])
    Q, proj = quo(M, S)
    (G, H, emb, phi, I, iemb, M, proj, Any[G, M, phi, proj, G, M])
end
"""
        )

        self.assertEqual(embedding.domain().identity_key(), subgroup.identity_key())
        self.assertEqual(embedding.codomain().identity_key(), group.identity_key())
        self.assertEqual(homomorphism.domain().identity_key(), group.identity_key())
        self.assertEqual(homomorphism.codomain().identity_key(), group.identity_key())
        self.assertEqual(image_embedding.domain().identity_key(), image.identity_key())
        self.assertEqual(self.bridge.call("order", subgroup), ZZ(12))
        self.assertEqual(projection.domain().identity_key(), module.identity_key())
        self.assertEqual(repeated[0].identity_key(), repeated[4].identity_key())
        self.assertEqual(repeated[1].identity_key(), repeated[5].identity_key())
        self.assertEqual(repeated[2].identity_key(), homomorphism.identity_key())
        self.assertEqual(repeated[3].identity_key(), projection.identity_key())


if __name__ == "__main__":
    unittest.main()
