FROM python:3.11-slim-bookworm@sha256:9c6f90801e6b68e772b7c0ca74260cbf7af9f320acec894e26fccdaccfbe3b47

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade "pip>=26.0.1" "setuptools>=80.9.0" "wheel>=0.46.2" && \
    pip install --no-cache-dir -r requirements.txt

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
