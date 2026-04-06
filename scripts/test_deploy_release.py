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


if __name__ == "__main__":
    unittest.main()
