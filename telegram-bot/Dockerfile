FROM python:3.11-slim@sha256:b1b81d67b8df73bf6067191790c233ef9e598b863fc1ccf447956138ca466d99

WORKDIR /app

RUN pip install --no-cache-dir --upgrade "pip>=26.0.1" "setuptools>=80.9.0" "wheel>=0.46.2" && \
    pip install --no-cache-dir python-dotenv==1.2.1 redis==6.4.0

COPY main.py .

RUN rm -rf /usr/local/lib/python3.11/ensurepip && \
    find /usr/local/lib/python3.11/site-packages -type d -path '*/site-packages/*.dist-info/sboms' -prune -exec rm -rf {} + && \
    find /usr/local/lib/python3.11/site-packages -type d \( \
      -name 'pip-*.dist-info' -o \
      -name 'setuptools-*.dist-info' -o \
      -name 'wheel-*.dist-info' -o \
      -path '*/site-packages/setuptools/_vendor/*.dist-info' \
    \) -prune -exec rm -rf {} + && \
    groupadd --system app && useradd --system --gid app --create-home --home-dir /home/app --shell /usr/sbin/nologin app && \
    chown -R app:app /app /home/app /tmp

ENV PYTHONDONTWRITEBYTECODE=1

USER app:app

CMD ["python", "main.py"]
