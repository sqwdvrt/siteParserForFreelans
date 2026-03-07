SHELL := /bin/bash
.DEFAULT_GOAL := help

DC := docker compose
DC_MON := docker compose -f docker-compose.yml -f docker-compose.monitoring.yml
MONITORING_SERVICES := prometheus alertmanager redis-exporter postgres-exporter grafana

POSTGRES_USER ?= site_parser
POSTGRES_DB ?= site_parser
POSTGRES_PORT ?= 55432

SERVICE ?=
FILE ?=

_BOLD := $(shell tput bold 2>/dev/null || true)
_GREEN := $(shell tput setaf 2 2>/dev/null || true)
_CYAN := $(shell tput setaf 6 2>/dev/null || true)
_RESET := $(shell tput sgr0 2>/dev/null || true)

define ok
	@echo "$(_GREEN)✔ $(1)$(_RESET)"
endef

.PHONY: help
help: ## Показать все доступные команды
	@echo ""
	@echo "$(_BOLD)Быстрый старт для человека$(_RESET)"
	@echo "  1. make init-env"
	@echo "  2. открой .env и заполни переменные"
	@echo "  3. make start"
	@echo "  4. make doctor"
	@echo "  5. make status"
	@echo "  6. make urls"
	@echo ""
	@echo "$(_BOLD)Если нужен локальный Telegram-бот$(_RESET)"
	@echo "  make start-bot"
	@echo ""
	@echo "$(_BOLD)Самые нужные команды$(_RESET)"
	@echo "  make start           # запустить проект"
	@echo "  make stop            # остановить проект"
	@echo "  make doctor          # проверить, что все живо"
	@echo "  make status          # посмотреть контейнеры"
	@echo "  make logs SERVICE=backend-api"
	@echo "  make queues          # посмотреть очереди"
	@echo ""
	@awk 'BEGIN {FS = ":.*##"; printf "\n"} \
		/^[a-zA-Z0-9_.-]+:.*##/ { printf "  $(_GREEN)%-24s$(_RESET) %s\n", $$1, $$2 }' \
		$(MAKEFILE_LIST)
	@echo ""

.PHONY: check-deps
check-deps: ## Проверить docker / docker compose
	@command -v docker >/dev/null 2>&1 || { echo "docker не найден"; exit 1; }
	@docker compose version >/dev/null 2>&1 || { echo "docker compose v2 не найден"; exit 1; }
	$(call ok,Зависимости найдены)

.PHONY: check-env
check-env: ## Проверить наличие .env
	@test -f .env || { echo ".env не найден. Выполни: make init-env"; exit 1; }
	$(call ok,.env найден)

.PHONY: init-env
init-env: ## Создать .env из .env.example, если его еще нет
	@test -f .env || cp .env.example .env
	$(call ok,.env готов)

.PHONY: order
order: ## Показать рекомендуемый порядок запуска
	@echo "make init-env"
	@echo "# отредактировать .env"
	@echo "make start"
	@echo "make doctor"
	@echo "make status"
	@echo "make urls"
	@echo "# если нужен локальный bot:"
	@echo "make start-bot"

.PHONY: start
start: up-full ## Самая простая команда: запустить проект целиком без локального telegram-bot

.PHONY: start-all
start-all: up-full-with-bot ## Запустить проект целиком вместе с локальным telegram-bot

.PHONY: start-bot
start-bot: up-bot ## Поднять только локальный telegram-bot

.PHONY: stop
stop: down ## Самая простая команда: остановить проект

.PHONY: status
status: ps ## Самая простая команда: показать состояние контейнеров

.PHONY: doctor
doctor: health ## Самая простая команда: проверить, что проект здоров

.PHONY: queues
queues: queue-status ## Самая простая команда: показать очереди Redis

.PHONY: up-core
up-core: check-deps check-env ## Поднять core: postgres, redis, backend-api (+ backend-migrate one-shot)
	$(DC) up -d postgres redis backend-api
	$(call ok,Core запущен)

.PHONY: up-workers
up-workers: check-deps check-env ## Поднять все worker-сервисы из profile workers
	$(DC) --profile workers up -d
	$(call ok,Workers запущены)

.PHONY: up-bot
up-bot: check-deps check-env ## Поднять локальный telegram-bot из profile bot
	$(DC) --profile bot up -d telegram-bot
	$(call ok,Локальный telegram-bot запущен)

.PHONY: up-monitoring
up-monitoring: check-deps check-env ## Поднять monitoring: prometheus, alertmanager, grafana, exporters
	$(DC_MON) --profile monitoring up -d $(MONITORING_SERVICES)
	$(call ok,Monitoring запущен)

.PHONY: up-full
up-full: up-core up-workers up-monitoring ## Полный локальный запуск без локального telegram-bot
	$(call ok,Полный локальный запуск завершен)

.PHONY: up-full-with-bot
up-full-with-bot: up-core up-workers up-bot up-monitoring ## Полный локальный запуск вместе с локальным telegram-bot
	$(call ok,Полный запуск с telegram-bot завершен)

.PHONY: down
down: ## Остановить весь локальный стек вместе с monitoring
	$(DC_MON) down --remove-orphans
	$(call ok,Стек остановлен)

.PHONY: stop-monitoring
stop-monitoring: ## Остановить только monitoring-сервисы
	$(DC_MON) stop $(MONITORING_SERVICES)
	$(call ok,Monitoring остановлен)

.PHONY: restart-core
restart-core: ## Перезапустить core
	$(DC) restart postgres redis backend-api
	$(call ok,Core перезапущен)

.PHONY: restart-workers
restart-workers: ## Перезапустить workers
	$(DC) restart browser-service backend-crawler backend-notifier ai-service ai-ac-consumer ai-user-embed ollama
	$(call ok,Workers перезапущены)

.PHONY: restart-bot
restart-bot: ## Перезапустить локальный telegram-bot
	$(DC) restart telegram-bot
	$(call ok,telegram-bot перезапущен)

.PHONY: restart-monitoring
restart-monitoring: ## Перезапустить monitoring-сервисы
	$(DC_MON) restart $(MONITORING_SERVICES)
	$(call ok,Monitoring перезапущен)

.PHONY: ps
ps: ## Показать статус всех контейнеров
	$(DC_MON) ps

.PHONY: urls
urls: ## Показать локальные URL сервисов
	@echo "API:          http://127.0.0.1:8080"
	@echo "Browser:      http://127.0.0.1:8090/healthz"
	@echo "Prometheus:   http://127.0.0.1:9090"
	@echo "Alertmanager: http://127.0.0.1:9093"
	@echo "Grafana:      http://127.0.0.1:3000"
	@echo "Ollama:       http://127.0.0.1:11435"

.PHONY: health
health: health-core health-workers health-monitoring ## Полная health-проверка локального стека
	$(call ok,Health-проверки завершены)

.PHONY: health-core
health-core: ## Проверить core health endpoints
	curl -fsS http://127.0.0.1:8080/healthz
	curl -fsS http://127.0.0.1:8080/readyz
	$(call ok,Core health OK)

.PHONY: health-workers
health-workers: ## Проверить browser-service и очереди
	curl -fsS http://127.0.0.1:8090/healthz
	$(DC) exec -T redis sh -lc 'printf "ai-process=%s\n" "$$(redis-cli LLEN ai-process)"; printf "ac-batch=%s\n" "$$(redis-cli LLEN ac-batch)"; printf "match-notify=%s\n" "$$(redis-cli LLEN match-notify)"'
	$(call ok,Workers health OK)

.PHONY: health-monitoring
health-monitoring: ## Проверить Prometheus / Alertmanager / Grafana
	curl -fsS http://127.0.0.1:9090/api/v1/targets >/tmp/targets.json
	jq -r '.data.activeTargets[] | [.labels.job,.health] | @tsv' /tmp/targets.json
	curl -fsS http://127.0.0.1:9093/api/v2/status >/tmp/alertmanager-status.json
	curl -fsS http://127.0.0.1:3000/api/health
	$(call ok,Monitoring health OK)

.PHONY: logs
logs: ## Логи сервиса: make logs SERVICE=backend-api
	@test -n "$(SERVICE)" || { echo "Укажи SERVICE=..."; exit 1; }
	$(DC_MON) logs -f $(SERVICE)

.PHONY: logs-api
logs-api: ## Логи backend-api
	$(DC) logs -f backend-api

.PHONY: logs-crawler
logs-crawler: ## Логи backend-crawler
	$(DC) logs -f backend-crawler

.PHONY: logs-notifier
logs-notifier: ## Логи backend-notifier
	$(DC) logs -f backend-notifier

.PHONY: logs-ai
logs-ai: ## Логи ai-service
	$(DC) logs -f ai-service

.PHONY: logs-ac
logs-ac: ## Логи ai-ac-consumer
	$(DC) logs -f ai-ac-consumer

.PHONY: logs-bot
logs-bot: ## Логи telegram-bot
	$(DC) logs -f telegram-bot

.PHONY: logs-monitoring
logs-monitoring: ## Логи monitoring-сервисов
	$(DC_MON) logs -f $(MONITORING_SERVICES)

.PHONY: shell-db
shell-db: ## Открыть psql в postgres-контейнере
	$(DC) exec postgres psql -U $${POSTGRES_USER:-$(POSTGRES_USER)} -d $${POSTGRES_DB:-$(POSTGRES_DB)}

.PHONY: shell-redis
shell-redis: ## Открыть redis-cli
	$(DC) exec redis redis-cli

.PHONY: shell-api
shell-api: ## Зайти в backend-api контейнер
	$(DC) exec backend-api sh

.PHONY: shell-ai
shell-ai: ## Зайти в ai-service контейнер
	$(DC) exec ai-service sh

.PHONY: migrate
migrate: check-deps check-env ## Прогнать миграции вручную через backend-migrate
	$(DC) run --rm backend-migrate
	$(call ok,Миграции применены)

.PHONY: test
test: test-go test-ai test-bot test-monitoring ## Все основные тесты и config gate
	$(call ok,Все тесты завершены)

.PHONY: test-go
test-go: ## Go тесты backend
	cd backend && GOTOOLCHAIN=local GOMODCACHE=$$(pwd)/.gomodcache GOCACHE=$$(pwd)/.gocache go test ./... -count=1
	$(call ok,Go тесты прошли)

.PHONY: test-ai
test-ai: ## Python тесты ai-service
	./scripts/pytest_ai.sh -q
	$(call ok,AI тесты прошли)

.PHONY: test-bot
test-bot: ## Python тесты telegram-bot
	cd telegram-bot && python3 -m pytest -q
	$(call ok,Telegram-bot тесты прошли)

.PHONY: test-monitoring
test-monitoring: ## Проверка monitoring-конфигов
	./scripts/monitoring_config_check.sh
	$(call ok,Monitoring config прошел)

.PHONY: test-fast
test-fast: ## Быстрый набор: backend + ai без coverage
	$(MAKE) test-go
	$(MAKE) test-ai

.PHONY: smoke-e2e
smoke-e2e: check-deps check-env ## Полный E2E smoke
	./scripts/e2e_test.sh

.PHONY: backup
backup: ## Бэкап Postgres
	./scripts/backup_postgres.sh

.PHONY: restore
restore: ## Restore Postgres: make restore FILE=/path/to/dump.sql.gz
	@test -n "$(FILE)" || { echo "Укажи FILE=/path/to/dump.sql.gz"; exit 1; }
	./scripts/restore_postgres.sh "$(FILE)"

.PHONY: backup-smoke
backup-smoke: ## Smoke backup/restore на локальной compose-БД
	DATABASE_URL="postgres://$(POSTGRES_USER):$${POSTGRES_PASSWORD:-site_parser}@localhost:$(POSTGRES_PORT)/$(POSTGRES_DB)?sslmode=disable" ./scripts/backup_restore_smoke.sh

.PHONY: queue-status
queue-status: ## Показать размеры основных очередей Redis
	$(DC) exec -T redis sh -lc 'for k in ai-process ai-process:processing ai-process:dlq user-embed user-embed:processing user-embed:dlq ac-batch ac-batch:processing ac-batch:dlq match-notify match-notify:processing match-notify:dlq; do printf "%s=%s\n" "$$k" "$$(redis-cli LLEN "$$k")"; done'

.PHONY: dlq-status
dlq-status: ## Показать только DLQ очереди
	$(DC) exec -T redis sh -lc 'for k in ai-process:dlq user-embed:dlq ac-batch:dlq match-notify:dlq; do printf "%s=%s\n" "$$k" "$$(redis-cli LLEN "$$k")"; done'

.PHONY: clean
clean: ## Остановить стек и удалить контейнеры/сети/анонимные volume
	$(DC_MON) down -v --remove-orphans
	$(call ok,Очистка выполнена)

.PHONY: clean-all
clean-all: ## Полная очистка: контейнеры, volume, локальные образы
	$(DC_MON) down -v --remove-orphans --rmi local
	$(call ok,Полная очистка выполнена)
