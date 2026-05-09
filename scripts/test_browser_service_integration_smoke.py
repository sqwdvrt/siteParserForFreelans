from __future__ import annotations

import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
SMOKE_SCRIPT = ROOT_DIR / "scripts" / "browser_service_integration_smoke.sh"


class BrowserServiceIntegrationSmokeTest(unittest.TestCase):
    def test_auto_picked_service_port_must_not_reuse_fixture_port(self) -> None:
        script_text = SMOKE_SCRIPT.read_text(encoding="utf-8")

        self.assertIn('while [[ "${BROWSER_SERVICE_PORT}" = "${HTTP_SERVER_PORT}" ]]; do', script_text)
        self.assertIn('BROWSER_SERVICE_PORT="$(pick_port)"', script_text)

    def test_explicit_port_collision_fails_fast(self) -> None:
        script_text = SMOKE_SCRIPT.read_text(encoding="utf-8")

        self.assertIn(
            'ERROR: BROWSER_SMOKE_SERVICE_PORT must differ from BROWSER_SMOKE_FIXTURE_PORT',
            script_text,
        )


if __name__ == "__main__":
    unittest.main()
