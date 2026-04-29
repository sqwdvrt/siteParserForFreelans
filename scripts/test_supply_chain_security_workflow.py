from __future__ import annotations

import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT_DIR / ".github" / "workflows" / "supply-chain-security.yml"
SECURITY_SCRIPT_PATH = ROOT_DIR / "scripts" / "security_baseline_check.sh"
AI_DOCKERFILE_PATH = ROOT_DIR / "ai-service" / "Dockerfile"
PYTHON_BOOKWORM_IMAGE = (
    "python:3.11-slim-bookworm@"
    "sha256:9c6f90801e6b68e772b7c0ca74260cbf7af9f320acec894e26fccdaccfbe3b47"
)


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

    def test_python_services_pin_bookworm_runtime_base(self) -> None:
        ai_dockerfile = AI_DOCKERFILE_PATH.read_text(encoding="utf-8")
        bot_dockerfile = (ROOT_DIR / "telegram-bot" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn(f"FROM {PYTHON_BOOKWORM_IMAGE} AS builder", ai_dockerfile)
        self.assertIn(f"FROM {PYTHON_BOOKWORM_IMAGE}\n", ai_dockerfile)
        self.assertIn(f"FROM {PYTHON_BOOKWORM_IMAGE}", bot_dockerfile)

    def test_ai_runtime_model_preload_is_opt_in(self) -> None:
        ai_dockerfile = AI_DOCKERFILE_PATH.read_text(encoding="utf-8")

        self.assertIn("ARG PRELOAD_LOCAL_MODELS=0", ai_dockerfile)
        self.assertIn('if [ "${PRELOAD_LOCAL_MODELS}" != "1" ]; then', ai_dockerfile)
        self.assertIn("Skipping local model preload", ai_dockerfile)
        self.assertIn("COPY --from=builder /opt/models /opt/models", ai_dockerfile)

    def test_ai_runtime_installs_ca_certificates_for_remote_model_downloads(self) -> None:
        ai_dockerfile = AI_DOCKERFILE_PATH.read_text(encoding="utf-8")

        self.assertGreaterEqual(ai_dockerfile.count("ca-certificates"), 2)


if __name__ == "__main__":
    unittest.main()
