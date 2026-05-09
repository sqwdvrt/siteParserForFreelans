# Production Image Drift Guard Design

**Goal:** Исключить production rollout, который оставляет active release и реально запущенные контейнеры в несовместимом состоянии, и добавить регулярное обнаружение image drift на VPS.

## Problem

Текущий deploy path гарантирует только то, что:
- release worktree создан;
- `docker compose pull/up` был запущен;
- post-deploy gate проверяет health критичных сервисов.

Этого недостаточно. На production VPS уже возник кейс, где:
- `current/.env.production` указывает на новый digest-pinned `AI_IMAGE`;
- живые AI-контейнеры продолжают работать на старом локальном image tag;
- health checks остаются зелёными, поэтому drift не попадает в ops report.

## Requirements

- Production deploy должен быть `fail-closed`.
- `current` нельзя переключать на release, если live containers не соответствуют release image refs.
- VPS health report должен регулярно обнаруживать runtime image drift и поднимать `FAIL`.
- Решение должно покрывать full deploy и `ai-only` deploy.

## Design

### 1. Deploy-time verification

`scripts/deploy_release.sh` получает новый verification step после `docker compose up`:
- читает expected image refs из release-local `.env.production`;
- для каждого runtime service ищет live container через `docker compose ps -q`;
- сравнивает `docker inspect .Config.Image` c expected ref из env;
- завершает deploy ошибкой при любом mismatch или missing container.

Эта проверка выполняется до `switch_live_release`, поэтому symlink `current` не переключается на release, который не соответствует реально поднятым контейнерам.

### 2. VPS drift detection

`scripts/vps_health_report.py` получает новый check `runtime_image_drift`:
- читает expected refs из live `.env.production`;
- сопоставляет env vars с runtime services;
- сравнивает expected ref и `docker inspect .Config.Image`;
- помечает check как `FAIL`, если найден хотя бы один mismatch.

Этот check попадает в:
- `vps-health-report.json`;
- `vps_ops_report.sh` summary;
- Telegram notification path.

### 3. Test coverage

Нужны тесты на два уровня:
- script-level tests для `deploy_release.sh`, чтобы зафиксировать наличие fail-closed verification step;
- unit tests для `vps_health_report.py`, чтобы drift correctly produced `FAIL`.

## Rollout

1. Внести код и тесты.
2. Прогнать локальные targeted tests.
3. Повторно задеплоить production так, чтобы AI containers перешли на digest из active release.
4. Снять `vps_ops_report.sh` и проверить, что `runtime_image_drift` исчез.
