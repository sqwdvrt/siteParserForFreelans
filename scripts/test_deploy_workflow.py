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

    def test_manual_workflow_exposes_deploy_scope_input(self) -> None:
        workflow_text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("deploy_scope:", workflow_text)
        self.assertIn('default: full', workflow_text)
        self.assertIn("- ai-only", workflow_text)

    def test_resolve_target_exports_deploy_scope(self) -> None:
        workflow_text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("deploy_scope: ${{ steps.meta.outputs.deploy_scope }}", workflow_text)
        self.assertIn("INPUT_SCOPE: ${{ github.event.inputs.deploy_scope }}", workflow_text)
        self.assertIn('deploy_scope="${INPUT_SCOPE:-full}"', workflow_text)

    def test_publish_runtime_images_skips_non_ai_builds_for_ai_only(self) -> None:
        job_block = self.job_block("publish-runtime-images")

        self.assertIn("if: ${{ needs.resolve-target.outputs.deploy_scope == 'full' }}", job_block)
        self.assertIn(
            "if: ${{ needs.resolve-target.outputs.deploy_scope == 'full' || "
            "needs.resolve-target.outputs.deploy_scope == 'ai-only' }}",
            job_block,
        )

    def test_remote_deploy_branch_reuses_current_non_ai_images_for_ai_only(self) -> None:
        workflow_text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("DEPLOY_SCOPE", workflow_text)
        self.assertIn('if [ "${DEPLOY_SCOPE}" = "ai-only" ]; then', workflow_text)
        self.assertIn('current_env="${STATE_DIR}/current/.env.production"', workflow_text)
        self.assertIn('if [ ! -f "${current_env}" ] && [ -f "${BASE_PATH}/.env.production" ]; then', workflow_text)
        self.assertIn('current_env="${BASE_PATH}/.env.production"', workflow_text)
        self.assertIn('BACKEND_IMAGE="$(awk -F= \'/^BACKEND_IMAGE=/{print substr($0,15)}\' "${current_env}")"', workflow_text)
        self.assertIn('BROWSER_SERVICE_IMAGE="$(awk -F= \'/^BROWSER_SERVICE_IMAGE=/{print substr($0,23)}\' "${current_env}")"', workflow_text)
        self.assertIn('TELEGRAM_BOT_IMAGE="$(awk -F= \'/^TELEGRAM_BOT_IMAGE=/{print substr($0,20)}\' "${current_env}")"', workflow_text)
        self.assertIn('bash "${RUN_ROOT}/scripts/deploy_release.sh"', workflow_text)

    def test_ai_only_deploy_scope_rejects_non_ai_target_commits(self) -> None:
        workflow_text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn(
            'changed_paths="$(git -C "${RUN_ROOT}" diff-tree --no-commit-id --name-only -r "${DEPLOY_SHA}")"',
            workflow_text,
        )
        self.assertIn(
            'ai-service/*|.github/workflows/deploy.yml|scripts/test_deploy_workflow.py|docs/operations.md|docs/vps_deploy.md|README.md)',
            workflow_text,
        )
        self.assertIn('ERROR: ai-only deploy cannot include non-AI path:', workflow_text)


if __name__ == "__main__":
    unittest.main()
