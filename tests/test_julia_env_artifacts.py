from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
JULIA_ENV = ROOT / "src/sage_julia_bridge/julia_env"
SOURCES_SCRIPT = ROOT / "tests/julia_env_artifact_sources.jl"


class Issue17ArtifactSourcesTest(unittest.TestCase):
    """A fresh depot instantiates the worker project without files.gap-system.org."""

    def test_every_artifact_has_a_reliable_source(self) -> None:
        completed = subprocess.run(
            ["julia", f"--project={JULIA_ENV}", "--startup-file=no", "--color=no", str(SOURCES_SCRIPT)],
            capture_output=True,
            text=True,
            check=True,
        )
        sources = json.loads(completed.stdout)

        # GAP_pkg_crisp is the artifact whose only working source was
        # files.gap-system.org; GAP.jl ships it through Oscar.
        self.assertIn("GAP/GAP_pkg_crisp", sources)
        unsourced = sorted(name for name, source in sources.items() if source is None)
        self.assertEqual(unsourced, [])


if __name__ == "__main__":
    unittest.main()
