# =============================================================================
# Makefile — единая точка входа для управления проектом
# =============================================================================
# Использование: make <цель>   |   make help — полный список

SHELL := /bin/bash
.ONESHELL:
.DEFAULT_GOAL := help

# ── Переменные ────────────────────────────────────────────────────────────────
DC      := docker compose
DC_PROD := docker compose -f docker-compose.prod.yml
DC_MON  := docker compose -f docker-compose.monitoring.yml
TAG     ?= latest
SERVICE ?=
FILE    ?=

# Цвета (graceful fallback без tput)
_BOLD  := $(shell tput bold   2>/dev/null || true)
_GREEN := $(shell tput setaf 2 2>/dev/null || true)
_CYAN  := $(shell tput setaf 6 2>/dev/null || true)
_RESET := $(shell tput sgr0   2>/dev/null || true)

define ok
	@echo "$(_GREEN)✔ $(1)$(_RESET)"
endef

# ── Проверка зависимостей ─────────────────────────────────────────────────────
.PHONY: _check_deps
_check_deps:
	@command -v docker >/dev/null 2>&1 || { echo "❌ docker не найден"; exit 1; }
	@docker compose version >/dev/null 2>&1 || { echo "❌ docker compose v2 не найден"; exit 1; }

##@ Dev

.PHONY: up
up: _check_deps ## Запустить все сервисы (включая telegram-bot)
	$(DC) up -d
	$(call ok,Сервисы запущены)

.PHONY: up-dev
up-dev: _check_deps ## Запустить всё кроме telegram-bot (Railway держит бота)
	$(DC) up -d postgres redis backend-api backend-crawler backend-notifier ai-service ai-user-embed browser-service
	$(call ok,Dev-сервисы запущены (без telegram-bot))

.PHONY: down
down: ## Остановить все сервисы
	$(DC) down
	$(call ok,Сервисы остановлены)

.PHONY: restart
restart: down up ## Перезапустить все сервисы

.PHONY: build
build: _check_deps ## Собрать образы (--pull)
	$(DC) build --pull
	$(call ok,Образы собраны)

.PHONY: ps
ps: ## Статус контейнеров
	$(DC) ps

.PHONY: top
top: ## CPU/MEM контейнеров
	$(DC) top

##@ Логи

.PHONY: logs
logs: ## Все сервисы (make logs SERVICE=backend-api — один сервис)
	$(DC) logs -f $(SERVICE)

.PHONY: logs-api
logs-api: ## backend-api
	$(DC) logs -f backend-api

.PHONY: logs-crawler
logs-crawler: ## backend-crawler
	$(DC) logs -f backend-crawler

.PHONY: logs-notifier
logs-notifier: ## backend-notifier
	$(DC) logs -f backend-notifier

.PHONY: logs-ai
logs-ai: ## ai-service
	$(DC) logs -f ai-service

.PHONY: logs-bot
logs-bot: ## telegram-bot
	$(DC) logs -f telegram-bot

##@ Миграции

.PHONY: migrate
migrate: ## Применить все pending миграции (через сервис backend-migrate)
	@echo "$(_CYAN)Применение миграций...$(_RESET)"
	$(DC) run --rm -e RUN_MIGRATIONS=1 backend-migrate
	$(call ok,Миграции применены)

##@ Тесты

.PHONY: test
test: test-go test-ai ## Все тесты (Go + Python)

.PHONY: test-all
test-all: test ## Алиас для CI (совместимость)

.PHONY: full-check
full-check: ## Полная проверка (скрипт scripts/full_check.sh)
	bash ./scripts/full_check.sh

.PHONY: test-go
test-go: ## Go тесты (backend)
	@echo "$(_CYAN)Go tests...$(_RESET)"
	cd backend && go test ./... -count=1
	$(call ok,Go tests passed)

.PHONY: test-ai
test-ai: ## Python тесты (ai-service)
	@echo "$(_CYAN)AI tests...$(_RESET)"
	./scripts/pytest_ai.sh -v
	$(call ok,AI tests passed)

.PHONY: test-ai-fast
test-ai-fast: ## Python тесты без интеграционных (быстро)
	./scripts/pytest_ai.sh -v -m "not integration"

.PHONY: coverage
coverage: ## Проверка покрытия кода
	./scripts/check_coverage.sh

##@ Shell / отладка

.PHONY: shell-api
shell-api: ## bash → backend-api
	$(DC) exec backend-api bash

.PHONY: shell-ai
shell-ai: ## bash → ai-service
	$(DC) exec ai-service bash

.PHONY: shell-db
shell-db: ## psql → postgres
	$(DC) exec postgres psql -U $${POSTGRES_USER:-postgres} $${POSTGRES_DB:-app}

.PHONY: shell-redis
shell-redis: ## redis-cli → redis
	$(DC) exec redis redis-cli

##@ Production

.PHONY: deploy
deploy: ## Первый деплой (scripts/first-deploy.sh)
	./scripts/first-deploy.sh

.PHONY: rollback
rollback: ## Откат образа: make rollback TAG=v1.2.3
	@test "$(TAG)" != "latest" || { echo "❌ Укажите тег: make rollback TAG=v1.2.3"; exit 1; }
	./scripts/rollback.sh $(TAG)

.PHONY: prod-up
prod-up: ## Запустить production сервисы
	$(DC_PROD) up -d
	$(call ok,Production запущен)

.PHONY: prod-down
prod-down: ## Остановить production сервисы
	$(DC_PROD) down

.PHONY: prod-ps
prod-ps: ## Статус production контейнеров
	$(DC_PROD) ps

.PHONY: prod-logs
prod-logs: ## Логи production (make prod-logs SERVICE=backend-api)
	$(DC_PROD) logs -f $(SERVICE)

##@ DLQ / обслуживание

.PHONY: dlq-retry
dlq-retry: ## Переложить сообщения из DLQ обратно в очереди
	./scripts/dlq_retry.sh

.PHONY: backup
backup: ## Резервная копия PostgreSQL
	./scripts/backup_postgres.sh

.PHONY: restore
restore: ## Восстановить из бэкапа: make restore FILE=backup.sql
	./scripts/restore_postgres.sh $(FILE)

##@ Мониторинг

.PHONY: mon-up
mon-up: ## Запустить стек мониторинга (Prometheus / Grafana)
	$(DC_MON) up -d
	$(call ok,Мониторинг запущен)

.PHONY: mon-down
mon-down: ## Остановить стек мониторинга
	$(DC_MON) down

##@ Очистка

.PHONY: clean
clean: ## Остановить контейнеры + удалить анонимные тома
	$(DC) down -v --remove-orphans
	$(call ok,Очистка выполнена)

.PHONY: clean-all
clean-all: ## Остановить контейнеры, удалить тома + собранные образы
	$(DC) down -v --remove-orphans --rmi local
	$(call ok,Очистка и удаление образов выполнены)

##@ Справка

.PHONY: help
help: ## Показать этот список
	@echo ""
	@echo "$(_BOLD)Управление проектом$(_RESET)"
	@awk 'BEGIN {FS = ":.*##"; printf "\n"} \
	  /^##@/ { printf "\n$(_CYAN)%s$(_RESET)\n", substr($$0, 5) } \
	  /^[a-zA-Z0-9_-]+:.*##/ { printf "  $(_GREEN)%-18s$(_RESET) %s\n", $$1, $$2 }' \
	  $(MAKEFILE_LIST)
	@echo ""
