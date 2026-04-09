from __future__ import annotations

import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT_DIR / ".github" / "workflows" / "supply-chain-security.yml"


class SupplyChainSecurityWorkflowTest(unittest.TestCase):
    def test_backend_scan_uses_os_only_and_skips_grype(self) -> None:
        text = WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertIn("service: backend", text)
        self.assertIn("trivy_pkg_types: os", text)
        self.assertIn('run_grype: "false"', text)

    def test_python_images_keep_library_scanning(self) -> None:
        text = WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertIn("service: ai-service", text)
        self.assertIn("service: telegram-bot", text)
        self.assertGreaterEqual(text.count("trivy_pkg_types: os,library"), 2)
        self.assertGreaterEqual(text.count('run_grype: "true"'), 2)

    def test_trivy_uses_vuln_scanner_only(self) -> None:
        text = WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertIn("--scanners vuln", text)
        self.assertIn('--vuln-type "${{ matrix.trivy_pkg_types }}"', text)


if __name__ == "__main__":
    unittest.main()
