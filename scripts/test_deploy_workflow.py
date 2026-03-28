from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DEPLOY_WORKFLOW = ROOT_DIR / ".github" / "workflows" / "deploy.yml"


class DeployWorkflowTest(unittest.TestCase):
    def job_block(self, job_name: str) -> str:
        workflow_text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
        pattern = re.compile(
            rf"^  {re.escape(job_name)}:\n(?P<body>(?:^(?!  [a-z0-9-]+:).*\n?)*)",
            re.MULTILINE,
        )
        match = pattern.search(workflow_text)
        self.assertIsNotNone(match, f"workflow is missing job block {job_name!r}")
        return match.group("body")

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

    def test_release_preflight_runs_for_manual_production_path(self) -> None:
        job_block = self.job_block("release-preflight-gate")

        self.assertIn(
            "if: ${{ needs.resolve-target.outputs.run_staging == 'true' || "
            "needs.resolve-target.outputs.run_production == 'true' }}",
            job_block,
        )

    def test_publish_runtime_images_runs_for_manual_production_path(self) -> None:
        job_block = self.job_block("publish-runtime-images")

        self.assertIn(
            "if: ${{ needs.resolve-target.outputs.run_staging == 'true' || "
            "needs.resolve-target.outputs.run_production == 'true' }}",
            job_block,
        )


if __name__ == "__main__":
    unittest.main()
