# VPS Health Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add repo-native VPS health automation with shared JSON/log output, Telegram-on-degradation, local SSH summary wrapper, and `systemd`/`crontab` installation paths.

**Architecture:** Reuse the existing production post-deploy gate and monitoring stack instead of building a second source of truth. Keep the recurring health job on the VPS, but store runnable helpers in deploy shared storage so automation survives release switches and can fall back to user `crontab` when rootless.

**Tech Stack:** Python 3 standard library, Bash, systemd timer/service, existing Docker Compose and Prometheus endpoints

---

### Task 1: Add failing tests for report logic and assets

**Files:**
- Create: `scripts/test_vps_health_report.py`
- Create: `scripts/test_vps_health_assets.py`

- [ ] **Step 1: Write failing tests for status derivation and summary output**
- [ ] **Step 2: Run `python3 -m unittest scripts/test_vps_health_report.py scripts/test_vps_health_assets.py` and verify missing-file failures**
- [ ] **Step 3: Add minimal implementation files**
- [ ] **Step 4: Re-run the same tests and verify they pass**

### Task 2: Implement VPS-side report generator and operator wrappers

**Files:**
- Create: `scripts/vps_health_report.py`
- Create: `scripts/vps_ops_report.sh`
- Create: `scripts/vps_safe_docker_cleanup.sh`

- [ ] **Step 1: Implement pure helper functions for status derivation, fingerprinting, and summary formatting**
- [ ] **Step 2: Implement report collection using `post_deploy_production_gate.sh`, Prometheus API, and host resource commands**
- [ ] **Step 3: Implement local SSH wrapper and safe Docker cleanup helper**
- [ ] **Step 4: Re-run targeted tests**

### Task 3: Add install assets and docs

**Files:**
- Create: `scripts/install_vps_health_report_timer.sh`
- Create: `ops/systemd/siteparser-vps-health-report.service`
- Create: `ops/systemd/siteparser-vps-health-report.timer`
- Modify: `.env.production.example`
- Modify: `README.md`
- Modify: `docs/vps_deploy.md`
- Modify: `docs/superpowers/specs/2026-04-15-vps-health-automation-design.md`

- [ ] **Step 1: Add `systemd` service/timer files, shared `ops/bin` runtime path, and installer script with rootless fallback**
- [ ] **Step 2: Document optional Telegram override env vars**
- [ ] **Step 3: Document install/run/operator commands in README and VPS guide**
- [ ] **Step 4: Re-run targeted tests and one final verification pass**
