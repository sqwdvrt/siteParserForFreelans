from __future__ import annotations

import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = ROOT_DIR / "scripts" / "deploy_release.sh"


class DeployReleaseScriptTest(unittest.TestCase):
    def test_release_defaults_include_monitoring_compose_and_profile(self) -> None:
        script_text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn(
            'COMPOSE_FILES=("docker-compose.prod.yml" "docker-compose.ssl.yml" "docker-compose.monitoring.yml")',
            script_text,
        )
        self.assertIn('COMPOSE_PROFILES=("monitoring")', script_text)
        self.assertIn(
            'MONITORING_SERVICES=("prometheus" "alertmanager" "redis-exporter" "postgres-exporter" "grafana")',
            script_text,
        )

    def test_compose_commands_use_stable_project_name(self) -> None:
        script_text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn(
            'docker compose -p "$COMPOSE_PROJECT_NAME" --env-file "$ENV_FILE"',
            script_text,
        )

    def test_release_env_persists_compose_profiles(self) -> None:
        script_text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn('COMPOSE_PROFILES=$(IFS=,; printf \'%s\' "${COMPOSE_PROFILES[*]}")', script_text)

    def test_compose_commands_enable_profiles(self) -> None:
        script_text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn('compose_args+=(--profile "$compose_profile")', script_text)

    def test_project_name_is_normalized_to_lowercase(self) -> None:
        script_text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("tr '[:upper:]' '[:lower:]'", script_text)

    def test_long_compose_steps_emit_heartbeat_logs(self) -> None:
        script_text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn('heartbeat_interval="${DEPLOY_HEARTBEAT_INTERVAL_SEC:-20}"', script_text)
        self.assertIn('log "${label}: still running"', script_text)
        self.assertIn('run_with_heartbeat "docker compose pull"', script_text)
        self.assertIn('run_with_heartbeat "docker compose up"', script_text)
        self.assertIn('run_with_heartbeat "docker compose monitoring recreate"', script_text)

    def test_monitoring_services_are_force_recreated_when_present(self) -> None:
        script_text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn('mapfile -t available_services < <(', script_text)
        self.assertIn('if [[ "${#monitoring_services_to_recreate[@]}" -gt 0 ]]; then', script_text)
        self.assertIn('up -d --no-build --force-recreate "${monitoring_services_to_recreate[@]}"', script_text)

    def test_deploy_fails_when_expected_service_container_is_missing(self) -> None:
        script_text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("assert_compose_services_created()", script_text)
        self.assertIn('[[ "$available_service" = "backend-migrate" ]] && continue', script_text)
        self.assertIn('docker compose -p "$COMPOSE_PROJECT_NAME" --env-file "$ENV_FILE" "${compose_args_ref[@]}" ps -q "$available_service"', script_text)
        self.assertIn('die "compose service did not create a container: ${available_service}"', script_text)
        self.assertIn('assert_compose_services_created compose_args services_to_assert', script_text)

    def test_deploy_runs_safe_docker_cleanup_before_pulling_images(self) -> None:
        script_text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("run_safe_docker_cleanup()", script_text)
        self.assertLess(
            script_text.index('run_safe_docker_cleanup'),
            script_text.index('run_with_heartbeat "docker compose pull"'),
        )

    def test_deploy_can_limit_compose_to_selected_services(self) -> None:
        script_text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn('TARGET_SERVICES=()', script_text)
        self.assertIn('--service)', script_text)
        self.assertIn('TARGET_SERVICES+=("$2")', script_text)
        self.assertIn('"${TARGET_SERVICES[@]}"', script_text)
        self.assertIn('services_to_assert=("${TARGET_SERVICES[@]}")', script_text)

    def test_deploy_verifies_live_container_images_before_switching_release(self) -> None:
        script_text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("verify_runtime_service_images()", script_text)
        self.assertIn('docker inspect --format "{{.Config.Image}}" "$container_id"', script_text)
        self.assertIn('die "runtime image mismatch for ${service}: expected ${expected_image}, got ${actual_image}"', script_text)
        self.assertLess(
            script_text.index("verify_runtime_service_images compose_args services_to_assert"),
            script_text.index("switch_live_release"),
        )


if __name__ == "__main__":
    unittest.main()
