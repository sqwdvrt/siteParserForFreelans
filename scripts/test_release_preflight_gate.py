from __future__ import annotations

import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
PREFLIGHT_SCRIPT = ROOT_DIR / "scripts" / "release_preflight_gate.sh"


class ReleasePreflightGateScriptTest(unittest.TestCase):
    def test_production_compose_validation_includes_monitoring_profile(self) -> None:
        script_text = PREFLIGHT_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("-f docker-compose.prod.yml", script_text)
        self.assertIn("-f docker-compose.ssl.yml", script_text)
        self.assertIn("-f docker-compose.monitoring.yml", script_text)
        self.assertIn("--profile monitoring", script_text)


if __name__ == "__main__":
    unittest.main()
