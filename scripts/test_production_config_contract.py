from __future__ import annotations

import sys
import unittest
from pathlib import Path
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))

from production_config_contract import validate_value


class ValidateValueOciImageDigestTest(unittest.TestCase):
    def test_rejects_tag_only_image_ref(self) -> None:
        errors: list[str] = []

        validate_value("BACKEND_IMAGE", "ghcr.io/example/backend:prod", {"type": "oci_image_digest"}, errors)

        self.assertTrue(errors)
        self.assertIn("digest-pinned", errors[0])

    def test_accepts_digest_pinned_image_ref(self) -> None:
        errors: list[str] = []

        validate_value(
            "BACKEND_IMAGE",
            "ghcr.io/example/backend@sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            {"type": "oci_image_digest"},
            errors,
        )

        self.assertEqual(errors, [])


class ValidateValueExistingFileTest(unittest.TestCase):
    def test_rejects_missing_file(self) -> None:
        errors: list[str] = []

        validate_value(
            "API_TLS_CERT_HOST_PATH",
            "/tmp/does-not-exist/fullchain.pem",
            {"type": "existing_file"},
            errors,
        )

        self.assertTrue(errors)
        self.assertIn("existing file", errors[0])

    def test_accepts_existing_file(self) -> None:
        errors: list[str] = []

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "fullchain.pem"
            path.write_text("dummy", encoding="utf-8")

            validate_value("API_TLS_CERT_HOST_PATH", str(path), {"type": "existing_file"}, errors)

        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
