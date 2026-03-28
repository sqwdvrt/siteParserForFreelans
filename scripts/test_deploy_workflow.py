from __future__ import annotations

import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DEPLOY_WORKFLOW = ROOT_DIR / ".github" / "workflows" / "deploy.yml"


class DeployWorkflowTest(unittest.TestCase):
    def test_manual_production_does_not_force_staging(self) -> None:
        workflow_text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn('run_staging="false"', workflow_text)
        self.assertIn(
            'if [ "${target_environment}" = "production" ]; then\n'
            '            run_staging="false"\n'
            '            run_production="true"',
            workflow_text,
        )

    def test_production_jobs_allow_skipped_staging_path(self) -> None:
        workflow_text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("always()", workflow_text)
        self.assertIn("needs.resolve-target.outputs.run_staging != 'true'", workflow_text)

    def test_missing_staging_secrets_disable_staging_path(self) -> None:
        workflow_text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn('staging_configured="true"', workflow_text)
        self.assertIn(
            'if [ "${run_staging}" = "true" ] && [ "${staging_configured}" != "true" ]; then',
            workflow_text,
        )


if __name__ == "__main__":
    unittest.main()
