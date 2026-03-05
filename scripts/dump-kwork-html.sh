#!/bin/bash
# Сохраняет реально отрендеренный HTML страницы Kwork в testdata
# Запуск: ./scripts/dump-kwork-html.sh [project_id]
set -euo pipefail

PROJECT_ID="${1:-3114999}"
BROWSER_URL="${BROWSER_URL:-http://localhost:8090}"
OUT_LIST="backend/testdata/kwork_list_rendered.html"
OUT_DETAIL="backend/testdata/kwork_detail_rendered.html"

echo "==> Рендер списка проектов..."
curl -sf "${BROWSER_URL}/render?url=https://kwork.ru/projects" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['html'])" \
  > "$OUT_LIST"
echo "    Сохранено: $OUT_LIST ($(wc -c < "$OUT_LIST") байт)"

echo "==> Рендер страницы проекта ${PROJECT_ID}..."
curl -sf "${BROWSER_URL}/render?url=https://kwork.ru/projects/${PROJECT_ID}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['html'])" \
  > "$OUT_DETAIL"
echo "    Сохранено: $OUT_DETAIL ($(wc -c < "$OUT_DETAIL") байт)"

echo ""
echo "==> Анализ структуры детальной страницы:"
python3 << 'EOF'
import re
html = open("backend/testdata/kwork_detail_rendered.html").read()

print("--- H1 теги ---")
for m in re.findall(r'<h1([^>]*)>(.*?)</h1>', html, re.DOTALL):
    text = re.sub(r'<[^>]+>', '', m[1]).strip()
    if text:
        print(f"  h1{m[0][:60]}: {text[:100]}")

print("\n--- Классы с want/title/price/budget/skill/desc ---")
classes = re.findall(r'class="([^"]*(?:want|title|price|budget|skill|desc)[^"]*)"', html, re.I)
for c in sorted(set(classes))[:25]:
    print(f"  .{c}")

print("\n--- <title> страницы ---")
t = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL)
if t:
    print(f"  {t.group(1).strip()[:120]}")
EOF
