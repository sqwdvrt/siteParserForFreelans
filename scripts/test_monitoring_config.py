from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


class PrometheusTelegramBotScrapeTest(unittest.TestCase):
    def test_rendered_prometheus_config_includes_telegram_bot_scrape_job(self) -> None:
        root = Path(__file__).resolve().parent.parent
        template = root / "monitoring/prometheus/prometheus.yml.tmpl"
        render_script = root / "monitoring/prometheus/render_config.sh"

        env = os.environ.copy()
        env.setdefault("BACKEND_API_METRICS_TARGET", "backend-api:8080")
        env.setdefault("BACKEND_API_METRICS_SCHEME", "http")
        env.setdefault("BACKEND_API_METRICS_TLS_INSECURE_SKIP_VERIFY", "false")

        with tempfile.TemporaryDirectory() as tmp_dir:
            rendered_path = Path(tmp_dir) / "prometheus.yml"
            subprocess.run(
                ["sh", str(render_script), str(template), str(rendered_path)],
                check=True,
                cwd=root,
                env=env,
            )
            rendered = rendered_path.read_text(encoding="utf-8")

        self.assertIn("- job_name: telegram-bot", rendered)
        self.assertIn("- telegram-bot:9107", rendered)


if __name__ == "__main__":
    unittest.main()
