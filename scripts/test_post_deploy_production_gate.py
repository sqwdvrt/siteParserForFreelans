from __future__ import annotations

import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
GATE_SCRIPT = ROOT_DIR / "scripts" / "post_deploy_production_gate.sh"


class PostDeployProductionGateScriptTest(unittest.TestCase):
    def test_host_side_curl_clears_container_only_ca_env_vars(self) -> None:
        script_text = GATE_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("env -u SSL_CERT_FILE -u CURL_CA_BUNDLE -u REQUESTS_CA_BUNDLE", script_text)


if __name__ == "__main__":
    unittest.main()
