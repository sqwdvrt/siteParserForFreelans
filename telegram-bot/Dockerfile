FROM python:3.11-slim@sha256:0b23cfb7425d065008b778022a17b1551c82f8b4866ee5a7a200084b7e2eafbf

WORKDIR /app

RUN pip install --no-cache-dir --upgrade "pip>=26.0.1" "setuptools>=80.9.0" "wheel>=0.46.2" && \
    pip install --no-cache-dir python-dotenv==1.2.1

COPY main.py .

RUN groupadd --system app && useradd --system --gid app --create-home --home-dir /home/app --shell /usr/sbin/nologin app && \
    chown -R app:app /app /home/app /tmp

ENV PYTHONDONTWRITEBYTECODE=1

USER app:app

CMD ["python", "main.py"]
