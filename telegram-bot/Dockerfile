FROM python:3.11-slim@sha256:6d98ca198cea726f2c86da2699594339a7b7ff08e49728797b4ed6e3b5c3b62a

WORKDIR /app

RUN pip install --no-cache-dir --upgrade "pip>=26.0.1" "setuptools>=80.9.0" "wheel>=0.46.2" && \
    pip install --no-cache-dir python-dotenv==1.2.1 redis==6.4.0

COPY main.py .

RUN groupadd --system app && useradd --system --gid app --create-home --home-dir /home/app --shell /usr/sbin/nologin app && \
    chown -R app:app /app /home/app /tmp

ENV PYTHONDONTWRITEBYTECODE=1

USER app:app

CMD ["python", "main.py"]
