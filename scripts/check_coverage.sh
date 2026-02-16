#!/bin/bash
# Проверка покрытия тестами (9.10, 9.11)
# Go: internal packages ≥70%, Python: src ≥70%

set -e
cd "$(dirname "$0")/.."

echo "=== Go tests (backend) ==="
cd backend
[ -f ../.env ] && set -a && source ../.env && set +a
go test ./... -cover 2>&1 | grep -E "^(ok|FAIL|\?)" || true
echo ""
echo "Coverage (internal packages):"
go test ./internal/... -coverprofile=/tmp/go_cover.out 2>/dev/null && go tool cover -func=/tmp/go_cover.out | tail -1

echo ""
echo "=== Python tests (ai-service) ==="
cd ../ai-service
python3 -m pytest --cov=src --cov-report=term-missing -q 2>&1 | tail -5
