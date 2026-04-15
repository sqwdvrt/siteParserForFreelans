# VPS Health Automation Design

**Goal:** Автоматизировать регулярную проверку production VPS, чтобы хост сам писал machine-readable отчёт, слал Telegram только при деградации и оставлял оператору короткий локальный SSH entrypoint для сводки и безопасного cleanup.

## Scope

- VPS-side health-report generator inside the repo.
- Shared JSON + append-only log on the VPS.
- Stable shared `ops/bin` runtime path on the VPS so automation survives release switches.
- Telegram notification on `WARN/FAIL` with noise suppression by fingerprint.
- Local operator wrapper that refreshes the remote report over SSH and prints a concise summary.
- `systemd` timer installation assets kept in the repo, with user `crontab` fallback when root access is unavailable.

## Architecture

`scripts/vps_health_report.py` is shipped from the repo into a stable VPS runtime path under shared state, sources `.env.production` from the live repo symlink, and reuses existing production checks:
- `scripts/post_deploy_production_gate.sh`
- Prometheus alerts/targets API on loopback
- queue depth metrics from `redis-exporter`
- host resource checks (`uptime`, `df`, `free`)

The report is written into deploy shared state:
- `/home/deploy/app/.siteParserForFreelans-deploy/shared/ops/bin/vps_health_report.py`
- `/home/deploy/app/.siteParserForFreelans-deploy/shared/ops/bin/vps_safe_docker_cleanup.sh`
- `/home/deploy/app/.siteParserForFreelans-deploy/shared/ops/vps-health-report.json`
- `/home/deploy/app/.siteParserForFreelans-deploy/shared/ops/vps-health-report.log`

This avoids losing history or the latest state when `current` flips to a new release.

## Status Model

- `OK`: all checks are healthy.
- `WARN`: host pressure or non-zero queues/DLQ without a hard service failure.
- `FAIL`: production gate failure, Prometheus failure, or hard capacity breach.

Telegram is sent only when:
- current status is `WARN` or `FAIL`;
- and the fingerprint of non-OK checks differs from the previous report.

## Operator Flow

`scripts/vps_ops_report.sh` is the local entrypoint:
- by default it refreshes the remote report over SSH, then fetches the JSON and renders a concise summary locally;
- with `--cleanup-docker` it triggers `scripts/vps_safe_docker_cleanup.sh` on the VPS before re-reading the report.

## Deployment

`scripts/install_vps_health_report_timer.sh` always installs runtime files into shared `ops/bin`.
If the installer runs with root privileges, it additionally installs `ops/systemd/siteparser-vps-health-report.service` and `.timer`.
If root privileges are unavailable, it installs an equivalent `*/5 * * * *` user `crontab` entry instead.
