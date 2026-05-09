# Production Image Drift Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Сделать production deploy fail-closed по image refs и добавить регулярное обнаружение runtime image drift на VPS

**Architecture:** `deploy_release.sh` будет валидировать live container image refs до переключения `current`, а `vps_health_report.py` будет независимо проверять тот же инвариант в регулярном health report. Это закрывает и prevention, и detection.

**Tech Stack:** Bash, Docker Compose, Python 3, `unittest`

---

### Task 1: Зафиксировать red tests

**Files:**
- Modify: `scripts/test_deploy_release.py`
- Modify: `scripts/test_vps_health_report.py`

- [ ] **Step 1: Добавить failing test на deploy verification step**
- [ ] **Step 2: Запустить `python3 -m unittest scripts.test_deploy_release` и убедиться, что новый тест падает**
- [ ] **Step 3: Добавить failing test на `runtime_image_drift` в health report**
- [ ] **Step 4: Запустить `python3 -m unittest scripts.test_vps_health_report` и убедиться, что новый тест падает**

### Task 2: Реализовать fail-closed deploy verification

**Files:**
- Modify: `scripts/deploy_release.sh`

- [ ] **Step 1: Добавить mapping runtime services -> env image vars**
- [ ] **Step 2: Реализовать compare expected/live image ref через `docker compose ps -q` и `docker inspect`**
- [ ] **Step 3: Вставить verification step после `docker compose up` и до `switch_live_release`**
- [ ] **Step 4: Запустить `python3 -m unittest scripts.test_deploy_release` и получить green**

### Task 3: Реализовать health drift detection

**Files:**
- Modify: `scripts/vps_health_report.py`
- Modify: `docs/operations.md`

- [ ] **Step 1: Добавить helper для сбора expected/live image refs**
- [ ] **Step 2: Добавить check `runtime_image_drift` со статусом `FAIL` при mismatch**
- [ ] **Step 3: Обновить ops doc, чтобы fail-closed invariant был описан в runbook**
- [ ] **Step 4: Запустить `python3 -m unittest scripts.test_vps_health_report` и получить green**

### Task 4: Финальная верификация и rollout

**Files:**
- Modify: `docs/vps_deploy.md` (если понадобится уточнение поведения)

- [ ] **Step 1: Прогнать целевые test suites**
- [ ] **Step 2: Проверить diff на предмет лишних изменений**
- [ ] **Step 3: Обновить production VPS каноническим deploy path**
- [ ] **Step 4: Проверить `bash ./scripts/vps_ops_report.sh` и убедиться, что drift исчез**
