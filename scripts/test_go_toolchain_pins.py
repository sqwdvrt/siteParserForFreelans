from __future__ import annotations

import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
EXPECTED_GO_VERSION = "1.25.9"
EXPECTED_GO_TOOLCHAIN = f"go{EXPECTED_GO_VERSION}"
EXPECTED_GOLANG_IMAGE = (
    "golang:1.25.9-alpine3.22@"
    "sha256:2c16ac01b3d038ca2ed421d66cea489e3cb670c251b4f8bbcfad2ebfb75f884c"
)


class GoToolchainPinsTest(unittest.TestCase):
    def test_backend_go_mod_uses_patched_toolchain(self) -> None:
        go_mod = (ROOT_DIR / "backend" / "go.mod").read_text(encoding="utf-8")
        self.assertIn(f"toolchain {EXPECTED_GO_TOOLCHAIN}", go_mod)

    def test_backend_builder_image_uses_patched_go_digest(self) -> None:
        dockerfile = (ROOT_DIR / "backend" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn(f"FROM {EXPECTED_GOLANG_IMAGE} AS builder", dockerfile)

    def test_go_scripts_default_to_patched_toolchain(self) -> None:
        expected_occurrences = {
            "scripts/security_baseline_check.sh": EXPECTED_GO_TOOLCHAIN,
            "scripts/full_check.sh": EXPECTED_GO_TOOLCHAIN,
            "scripts/release_preflight_gate.sh": EXPECTED_GO_TOOLCHAIN,
            "scripts/go_test_backend.sh": EXPECTED_GO_TOOLCHAIN,
            "scripts/check_coverage.sh": EXPECTED_GO_TOOLCHAIN,
        }

        for rel_path, expected in expected_occurrences.items():
            with self.subTest(path=rel_path):
                text = (ROOT_DIR / rel_path).read_text(encoding="utf-8")
                self.assertIn(expected, text)

    def test_ci_workflows_pin_patched_go_toolchain(self) -> None:
        workflow_expectations = {
            ".github/workflows/security-baseline.yml": [
                'GO_TOOLCHAIN: go1.25.9',
            ],
            ".github/workflows/full-check.yml": [
                'GO_TOOLCHAIN: go1.25.9',
                'backend/.gomodcache-go1.25.9',
                'backend/.gocache-go1.25.9',
            ],
        }

        for rel_path, snippets in workflow_expectations.items():
            text = (ROOT_DIR / rel_path).read_text(encoding="utf-8")
            for snippet in snippets:
                with self.subTest(path=rel_path, snippet=snippet):
                    self.assertIn(snippet, text)


if __name__ == "__main__":
    unittest.main()
