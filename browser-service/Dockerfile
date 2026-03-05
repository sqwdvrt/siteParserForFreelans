# Официальный образ Playwright с предустановленным Chromium и системными зависимостями.
# Версия должна совпадать с playwright в requirements.txt.
FROM mcr.microsoft.com/playwright/python:v1.49.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

# Playwright-образ использует непривилегированного пользователя pwuser
USER pwuser

EXPOSE 8090

HEALTHCHECK --interval=10s --timeout=5s --retries=5 --start-period=30s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8090/healthz')"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8090", "--workers", "1"]
