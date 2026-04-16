from __future__ import annotations

import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT_DIR / "scripts" / "backup_vps_cron.sh"


class BackupVpsCronScriptTest(unittest.TestCase):
    def test_default_retention_days_is_7(self) -> None:
        script_text = SCRIPT_PATH.read_text(encoding="utf-8")

        self.assertIn('RETENTION_DAYS="${RETENTION_DAYS:-7}"', script_text)
        self.assertIn("ротация 7 дней", script_text)


if __name__ == "__main__":
    unittest.main()
