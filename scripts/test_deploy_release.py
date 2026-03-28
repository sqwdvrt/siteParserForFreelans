from __future__ import annotations

import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = ROOT_DIR / "scripts" / "deploy_release.sh"


class DeployReleaseScriptTest(unittest.TestCase):
    def test_compose_commands_use_stable_project_name(self) -> None:
        script_text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn(
            'docker compose -p "$COMPOSE_PROJECT_NAME" --env-file "$ENV_FILE"',
            script_text,
        )

    def test_project_name_is_normalized_to_lowercase(self) -> None:
        script_text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("tr '[:upper:]' '[:lower:]'", script_text)


if __name__ == "__main__":
    unittest.main()
