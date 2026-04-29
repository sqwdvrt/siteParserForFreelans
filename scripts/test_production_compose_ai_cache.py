from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
COMPOSE_PROD = ROOT_DIR / "docker-compose.prod.yml"


class ProductionComposeAICacheTest(unittest.TestCase):
    def test_ai_services_use_persistent_huggingface_cache(self) -> None:
        text = COMPOSE_PROD.read_text(encoding="utf-8")

        self.assertIn("x-ai-cache-volumes: &ai-cache-volumes", text)
        self.assertIn("ai-hf-cache:/tmp/hf", text)
        self.assertIn("ai-transformers-cache:/tmp/transformers", text)
        self.assertIn("ai-torch-cache:/tmp/torch", text)
        self.assertIn("ai-xdg-cache:/tmp/.cache", text)

        for service in ("ai-service", "ai-user-embed", "ai-user-rematch", "ai-ac-consumer"):
            self.assert_service_uses_cache_volumes(text, service)

        for volume in ("ai-hf-cache", "ai-transformers-cache", "ai-torch-cache", "ai-xdg-cache"):
            self.assertIn(f"  {volume}:", text)

    def assert_service_uses_cache_volumes(self, text: str, service: str) -> None:
        pattern = re.compile(
            rf"^  {re.escape(service)}:\n(?P<body>(?:^(?!  [a-z0-9-]+:).*\n?)*)",
            re.MULTILINE,
        )
        match = pattern.search(text)
        self.assertIsNotNone(match, f"missing service block for {service}")
        block = match.group(0)

        self.assertIn("    image: ${AI_IMAGE:?set_AI_IMAGE_in_.env.production}", block)
        self.assertIn("    volumes: *ai-cache-volumes", block)


if __name__ == "__main__":
    unittest.main()
