from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_production_immutability import (
    ProductionImmutabilityError,
    check_compose_services_are_image_only,
    check_example_image_refs_use_digests,
    check_text_has_no_build_flag,
)


class CheckComposeServicesAreImageOnlyTest(unittest.TestCase):
    def test_rejects_service_with_build_stanza(self) -> None:
        compose_text = textwrap.dedent(
            """
            services:
              backend-api:
                build:
                  context: ./backend
                image: ghcr.io/example/backend@sha256:deadbeef
              telegram-bot:
                image: ghcr.io/example/telegram-bot@sha256:feedface
            """
        )

        with self.assertRaises(ProductionImmutabilityError) as ctx:
            check_compose_services_are_image_only(compose_text, ["backend-api", "telegram-bot"])

        self.assertIn("backend-api", str(ctx.exception))

    def test_accepts_service_with_image_only(self) -> None:
        compose_text = textwrap.dedent(
            """
            services:
              backend-api:
                image: ghcr.io/example/backend@sha256:deadbeef
              telegram-bot:
                image: ghcr.io/example/telegram-bot@sha256:feedface
            """
        )

        check_compose_services_are_image_only(compose_text, ["backend-api", "telegram-bot"])


class CheckTextHasNoBuildFlagTest(unittest.TestCase):
    def test_rejects_build_flag(self) -> None:
        with self.assertRaises(ProductionImmutabilityError):
            check_text_has_no_build_flag("docker compose up -d --build", source="deploy")

    def test_accepts_pull_then_up(self) -> None:
        check_text_has_no_build_flag("docker compose pull && docker compose up -d", source="deploy")


class CheckExampleImageRefsUseDigestsTest(unittest.TestCase):
    def test_rejects_tag_only_reference(self) -> None:
        env_text = "BACKEND_IMAGE=ghcr.io/example/siteparserforfreelans-backend:prod\n"

        with self.assertRaises(ProductionImmutabilityError):
            check_example_image_refs_use_digests(env_text, ["BACKEND_IMAGE"])

    def test_accepts_digest_reference(self) -> None:
        env_text = (
            "BACKEND_IMAGE="
            "ghcr.io/example/siteparserforfreelans-backend@"
            "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef\n"
        )

        check_example_image_refs_use_digests(env_text, ["BACKEND_IMAGE"])


if __name__ == "__main__":
    unittest.main()
