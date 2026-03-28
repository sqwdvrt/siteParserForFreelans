from __future__ import annotations

import sys
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
