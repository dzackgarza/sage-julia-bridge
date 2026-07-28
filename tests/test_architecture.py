from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
PYTHON_KERNEL = (
    ROOT / "src/sage_julia_bridge/interface.py",
    ROOT / "src/sage_julia_bridge/conversion.py",
    ROOT / "src/sage_julia_bridge/errors.py",
)
JULIA_KERNEL = ROOT / "src/sage_julia_bridge/julia_bridge.jl"


class Issue11ArchitectureBoundaryTest(unittest.TestCase):
    def test_kernel_python_imports_only_downward_layers(self) -> None:
        forbidden = {
            "sage_julia_bridge.localization",
            "sage_julia_bridge.realization",
        }
        imported: set[str] = set()
        for path in PYTHON_KERNEL:
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module is not None:
                    imported.add(node.module)

        self.assertTrue(forbidden.isdisjoint(imported), imported & forbidden)

    def test_julia_kernel_has_no_domain_type_dispatch_or_named_adapter(self) -> None:
        source = JULIA_KERNEL.read_text()
        concrete_domain_types = (
            "MPolyLocRing",
            "FreeMod",
            "AbsAffineScheme",
            "ZZLat",
            "GAPGroup",
        )

        for concrete_type in concrete_domain_types:
            self.assertIsNone(re.search(rf"\bisa\s+{concrete_type}\b", source))
        self.assertNotIn("localization_backend.jl", source)
        self.assertIn('endswith(path, "_backend.jl")', source)


if __name__ == "__main__":
    unittest.main()
