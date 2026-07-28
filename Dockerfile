FROM python:3.12.11-slim-bookworm

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libffi-dev \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    libxml2 \
    libxslt1.1 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
COPY app ./app
RUN python -m pip install .

COPY workflows ./workflows

RUN groupadd --system app \
    && useradd --system --gid app --home-dir /app app \
    && chown -R app:app /app

USER app

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
