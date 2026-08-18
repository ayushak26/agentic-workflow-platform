FROM alpine:3.22 AS scientific-skills

ARG SCIENTIFIC_SKILLS_REF=v2.59.0

RUN apk add --no-cache ca-certificates git \
    && git clone \
        --branch "${SCIENTIFIC_SKILLS_REF}" \
        --depth 1 \
        https://github.com/K-Dense-AI/scientific-agent-skills.git \
        /scientific-agent-skills \
    && rm -rf /scientific-agent-skills/.git


FROM python:3.14.7-slim-bookworm

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

COPY pyproject.toml uv.lock ./
COPY app ./app
RUN python -m pip install uv \
    && uv sync --frozen --no-dev

COPY workflows ./workflows
COPY --from=scientific-skills \
    /scientific-agent-skills/skills \
    /opt/scientific-agent-skills/skills

RUN groupadd --system app \
    && useradd --system --uid 10001 --gid app --home-dir /app app \
    && chown -R app:app /app /opt/scientific-agent-skills \
    && mkdir -p /run/snippet-runner \
    && chown app:app /run/snippet-runner

USER app

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
