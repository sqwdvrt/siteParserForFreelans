from __future__ import annotations

import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT_DIR / ".github" / "workflows" / "supply-chain-security.yml"
SECURITY_SCRIPT_PATH = ROOT_DIR / "scripts" / "security_baseline_check.sh"


class SupplyChainSecurityWorkflowTest(unittest.TestCase):
    def test_all_images_use_os_only_and_skip_grype(self) -> None:
        text = WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertIn("service: backend", text)
        self.assertIn("service: ai-service", text)
        self.assertIn("service: telegram-bot", text)
        self.assertEqual(text.count("trivy_pkg_types: os"), 3)
        self.assertEqual(text.count('run_grype: "false"'), 3)
        self.assertIn("trivy_pkg_types: os", text)
        self.assertIn('run_grype: "false"', text)

    def test_trivy_uses_vuln_scanner_only(self) -> None:
        text = WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertIn("--scanners vuln", text)
        self.assertIn('--vuln-type "${{ matrix.trivy_pkg_types }}"', text)

    def test_security_baseline_audits_python_requirements(self) -> None:
        text = SECURITY_SCRIPT_PATH.read_text(encoding="utf-8")
        self.assertIn('"ai-service/requirements.txt"', text)
        self.assertIn('"telegram-bot/requirements.txt"', text)
        self.assertIn('python -m pip_audit --no-deps -r /repo/telegram-bot/requirements.txt', text)


if __name__ == "__main__":
    unittest.main()
