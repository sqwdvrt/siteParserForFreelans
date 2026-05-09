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

    def test_ai_runtime_publish_builds_slim_image_without_bundled_models(self) -> None:
        job_block = self.job_block("publish-runtime-images")

        self.assertIn("Build and push AI runtime image", job_block)
        self.assertIn("PRELOAD_LOCAL_MODELS=0", job_block)

    def test_remote_deploy_branch_reuses_current_non_ai_images_for_ai_only(self) -> None:
        workflow_text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("DEPLOY_SCOPE", workflow_text)
        self.assertIn('if [ "${DEPLOY_SCOPE}" = "ai-only" ]; then', workflow_text)
        self.assertIn('current_env="${STATE_DIR}/current/.env.production"', workflow_text)
        self.assertIn('if [ ! -f "${current_env}" ] && [ -f "${BASE_PATH}/.env.production" ]; then', workflow_text)
        self.assertIn('current_env="${BASE_PATH}/.env.production"', workflow_text)
        self.assertIn('BACKEND_IMAGE="$(grep \'^BACKEND_IMAGE=\' "${current_env}" | cut -d= -f2-)"', workflow_text)
        self.assertIn('BROWSER_SERVICE_IMAGE="$(grep \'^BROWSER_SERVICE_IMAGE=\' "${current_env}" | cut -d= -f2-)"', workflow_text)
        self.assertIn('TELEGRAM_BOT_IMAGE="$(grep \'^TELEGRAM_BOT_IMAGE=\' "${current_env}" | cut -d= -f2-)"', workflow_text)
        self.assertIn('bash "${RUN_ROOT}/scripts/deploy_release.sh"', workflow_text)

    def test_ai_only_deploy_scope_rejects_non_ai_target_commits(self) -> None:
        workflow_text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn(
            'changed_paths="$(git -C "${RUN_ROOT}" diff-tree --no-commit-id --name-only -r "${DEPLOY_SHA}")"',
            workflow_text,
        )
        self.assertIn(
            'ai-service/*|docker-compose.yml|docker-compose.prod.yml|docker-compose.ssl.yml|.github/workflows/deploy.yml|scripts/deploy_release.sh|scripts/post_deploy_ai_gate.sh|scripts/test_deploy_release.py|scripts/test_deploy_workflow.py|scripts/test_production_compose_ai_cache.py|scripts/test_supply_chain_security_workflow.py|docs/operations.md|docs/vps_deploy.md|README.md)',
            workflow_text,
        )
        self.assertIn('ERROR: ai-only deploy cannot include non-AI path:', workflow_text)

    def test_remote_deploy_uses_argument_array_and_env_paths(self) -> None:
        workflow_text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("BASE_PATH: ${{ secrets.STAGING_DEPLOY_PATH }}", workflow_text)
        self.assertIn("BASE_PATH: ${{ secrets.PROD_DEPLOY_PATH }}", workflow_text)
        self.assertIn('BASE_PATH="${BASE_PATH:?BASE_PATH is required}"', workflow_text)
        self.assertIn("POST_DEPLOY_GATE: scripts/post_deploy_production_gate.sh", workflow_text)
        self.assertIn("command_timeout: 90m", workflow_text)
        self.assertIn('set -- --base-path "${BASE_PATH}" --sha "${DEPLOY_SHA}" --origin-url "${ORIGIN_URL}"', workflow_text)
        self.assertIn('[ -z "${POST_DEPLOY_GATE:-}" ] || set -- "$@" --post-deploy-gate "${POST_DEPLOY_GATE}"', workflow_text)
        self.assertIn('bash "${RUN_ROOT}/scripts/deploy_release.sh" "$@"', workflow_text)

    def test_ai_only_deploy_uses_ai_post_deploy_gate(self) -> None:
        workflow_text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn('AI_POST_DEPLOY_GATE: scripts/post_deploy_ai_gate.sh', workflow_text)
        self.assertIn(
            'if [ "${DEPLOY_SCOPE}" = "ai-only" ]; then\n'
            '              set -- "$@" --post-deploy-gate "${AI_POST_DEPLOY_GATE}"',
            workflow_text,
        )
        self.assertIn(
            'else\n'
            '              [ -z "${POST_DEPLOY_GATE:-}" ] || set -- "$@" --post-deploy-gate "${POST_DEPLOY_GATE}"',
            workflow_text,
        )

    def test_ai_only_deploy_limits_compose_to_ai_services(self) -> None:
        workflow_text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn('if [ "${DEPLOY_SCOPE}" = "ai-only" ]; then', workflow_text)
        self.assertIn('set -- "$@" --service ai-service --service ai-user-embed --service ai-user-rematch --service ai-ac-consumer', workflow_text)

    def test_deploy_jobs_disable_drone_script_stop_for_multiline_shell(self) -> None:
        for job_name in ("deploy-staging", "staging-smoke-e2e-gate", "deploy-production"):
            job_block = self.job_block(job_name)
            self.assertIn("uses: appleboy/ssh-action@v1.2.0", job_block)
            self.assertIn("script_stop: false", job_block)


if __name__ == "__main__":
    unittest.main()
