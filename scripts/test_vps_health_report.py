from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT_DIR / "scripts" / "vps_health_report.py"


def load_module():
    spec = importlib.util.spec_from_file_location("vps_health_report", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class VpsHealthReportTest(unittest.TestCase):
    def test_collect_report_reads_queue_metrics_without_name_error(self) -> None:
        module = load_module()

        def fake_run_command(args, **kwargs):
            command = args[0]
            if command == "uptime":
                return subprocess.CompletedProcess(args, 0, " 19:41:01 up 31 days,  7:14, 10 users,  load average: 1.63, 0.75, 0.55\n", "")
            if command == "df":
                return subprocess.CompletedProcess(
                    args,
                    0,
                    "Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/sda1 50331648 40894464 9437184 81% /\n",
                    "",
                )
            if command == "free":
                return subprocess.CompletedProcess(
                    args,
                    0,
                    "               total        used        free      shared  buff/cache   available\nMem:            3915        1754         237         142        2358        2161\nSwap:           2047        1139         908\n",
                    "",
                )
            if command == "bash":
                return subprocess.CompletedProcess(args, 0, "[post-deploy-production-gate] All checks passed.\n", "")
            if command == "docker":
                return subprocess.CompletedProcess(args, 0, "restart_count=0 started=2026-04-15T19:40:43Z\n", "")
            raise AssertionError(f"unexpected command: {args}")

        def fake_load_json_from_url(base_url, path, query=None):
            if path == "/api/v1/alerts":
                return {"data": {"alerts": []}}
            if path == "/api/v1/targets":
                return {"data": {"activeTargets": [{"labels": {"job": "backend-api"}, "scrapePool": "backend-api", "health": "up"}]}}
            if path == "/api/v1/query":
                self.assertIsNotNone(query)
                return {
                    "data": {
                        "result": [
                            {"metric": {"key": "ai-process"}, "value": [0, "0"]},
                            {"metric": {"key": "ai-process:processing"}, "value": [0, "0"]},
                            {"metric": {"key": "ai-process:dlq"}, "value": [0, "0"]},
                        ]
                    }
                }
            raise AssertionError(f"unexpected path: {path}")

        module.run_command = fake_run_command
        module.load_json_from_url = fake_load_json_from_url

        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_root = Path(tmp_dir)
            (repo_root / ".env.production").write_text("", encoding="utf-8")
            args = Namespace(
                repo_root=str(repo_root),
                env_file=".env.production",
                prometheus_url="http://127.0.0.1:9090",
                disk_warn_percent=80,
                disk_fail_percent=90,
                swap_warn_mb=512,
                memory_available_warn_mb=1024,
            )

            report = module.collect_report(args)

        self.assertEqual(report["overall_status"], "WARN")
        self.assertEqual(report["metrics"]["queues"]["ai-process"], 0)

    def test_derive_overall_status_prefers_fail_then_warn(self) -> None:
        module = load_module()

        report = {
            "checks": [
                {"name": "disk", "status": "WARN", "summary": "disk is high"},
                {"name": "production_gate", "status": "OK", "summary": "healthy"},
            ]
        }
        self.assertEqual(module.derive_overall_status(report), "WARN")

        report["checks"].append({"name": "prometheus_alerts", "status": "FAIL", "summary": "alert firing"})
        self.assertEqual(module.derive_overall_status(report), "FAIL")

    def test_summarize_report_groups_non_ok_checks(self) -> None:
        module = load_module()

        report = {
            "generated_at": "2026-04-15T19:41:04Z",
            "host": "prod-vps",
            "overall_status": "WARN",
            "metrics": {
                "disk_root": {"used_percent": 81, "free_gb": 9.2},
                "memory": {"available_mb": 2161, "swap_used_mb": 1139},
            },
            "checks": [
                {"name": "production_gate", "status": "OK", "summary": "all checks passed"},
                {"name": "disk", "status": "WARN", "summary": "root filesystem is 81% used"},
                {"name": "swap", "status": "WARN", "summary": "swap usage is elevated"},
            ],
            "recommendations": ["Consider pruning unused Docker artifacts."],
        }

        summary = module.summarize_report(report)

        self.assertIn("Status: WARN", summary)
        self.assertIn("Host: prod-vps", summary)
        self.assertIn("root filesystem is 81% used", summary)
        self.assertIn("swap usage is elevated", summary)
        self.assertIn("Consider pruning unused Docker artifacts.", summary)

    def test_notification_fingerprint_changes_when_problem_summary_changes(self) -> None:
        module = load_module()

        report_a = {
            "overall_status": "WARN",
            "checks": [
                {"name": "disk", "status": "WARN", "summary": "root filesystem is 81% used"},
                {"name": "production_gate", "status": "OK", "summary": "healthy"},
            ],
        }
        report_b = {
            "overall_status": "WARN",
            "checks": [
                {"name": "disk", "status": "WARN", "summary": "root filesystem is 85% used"},
                {"name": "production_gate", "status": "OK", "summary": "healthy"},
            ],
        }

        self.assertNotEqual(
            module.notification_fingerprint(report_a),
            module.notification_fingerprint(report_b),
        )

    def test_summary_cli_reads_json_and_prints_operator_view(self) -> None:
        report = {
            "generated_at": "2026-04-15T19:41:04Z",
            "host": "prod-vps",
            "overall_status": "OK",
            "metrics": {
                "disk_root": {"used_percent": 54, "free_gb": 21.7},
                "memory": {"available_mb": 2400, "swap_used_mb": 0},
            },
            "checks": [
                {"name": "production_gate", "status": "OK", "summary": "all checks passed"},
                {"name": "prometheus_alerts", "status": "OK", "summary": "no active alerts"},
            ],
            "recommendations": [],
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            report_path = Path(tmp_dir) / "report.json"
            report_path.write_text(json.dumps(report), encoding="utf-8")

            completed = subprocess.run(
                [sys.executable, str(MODULE_PATH), "summary", "--input-json", str(report_path)],
                check=True,
                capture_output=True,
                text=True,
                cwd=ROOT_DIR,
            )

        self.assertIn("Status: OK", completed.stdout)
        self.assertIn("no active alerts", completed.stdout)


if __name__ == "__main__":
    unittest.main()
