FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir python-dotenv

COPY main.py .

RUN groupadd --system app && useradd --system --gid app --create-home --home-dir /home/app --shell /usr/sbin/nologin app && \
    chown -R app:app /app /home/app /tmp

ENV PYTHONDONTWRITEBYTECODE=1

USER app:app

CMD ["python", "main.py"]
