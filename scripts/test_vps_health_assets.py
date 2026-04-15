from __future__ import annotations

import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
INSTALL_SCRIPT = ROOT_DIR / "scripts" / "install_vps_health_report_timer.sh"
SERVICE_FILE = ROOT_DIR / "ops" / "systemd" / "siteparser-vps-health-report.service"
TIMER_FILE = ROOT_DIR / "ops" / "systemd" / "siteparser-vps-health-report.timer"
WRAPPER_SCRIPT = ROOT_DIR / "scripts" / "vps_ops_report.sh"


class VpsHealthAssetsTest(unittest.TestCase):
    def test_systemd_service_runs_health_report_generator(self) -> None:
        service_text = SERVICE_FILE.read_text(encoding="utf-8")

        self.assertIn(".siteParserForFreelans-deploy/shared/ops/bin/vps_health_report.py", service_text)
        self.assertIn("--repo-root /home/deploy/app/siteParserForFreelans", service_text)
        self.assertIn(".siteParserForFreelans-deploy/shared/ops", service_text)

    def test_systemd_timer_runs_every_five_minutes(self) -> None:
        timer_text = TIMER_FILE.read_text(encoding="utf-8")

        self.assertIn("OnCalendar=*:0/5", timer_text)
        self.assertIn("Persistent=true", timer_text)

    def test_install_script_installs_and_enables_timer(self) -> None:
        script_text = INSTALL_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("siteparser-vps-health-report.service", script_text)
        self.assertIn("systemctl daemon-reload", script_text)
        self.assertIn("systemctl enable --now siteparser-vps-health-report.timer", script_text)
        self.assertIn("install_user_cron", script_text)
        self.assertIn("crontab -l", script_text)

    def test_local_wrapper_refreshes_remote_report_and_prints_summary(self) -> None:
        wrapper_text = WRAPPER_SCRIPT.read_text(encoding="utf-8")

        self.assertIn('REMOTE_BIN_DIR="${REMOTE_BIN_DIR:-/home/deploy/app/.siteParserForFreelans-deploy/shared/ops/bin}"', wrapper_text)
        self.assertIn('python3 \\"$REMOTE_BIN_DIR/vps_health_report.py\\" generate', wrapper_text)
        self.assertIn('>/dev/null', wrapper_text)
        self.assertIn("cat /home/deploy/app/.siteParserForFreelans-deploy/shared/ops/vps-health-report.json", wrapper_text)
        self.assertIn("python3 ./scripts/vps_health_report.py summary --input-json", wrapper_text)

    def test_install_script_copies_runtime_files_into_shared_ops_bin(self) -> None:
        script_text = INSTALL_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("SHARED_OPS_BIN", script_text)
        self.assertIn("install -D -m 0755", script_text)
        self.assertIn("vps_health_report.py", script_text)


if __name__ == "__main__":
    unittest.main()
