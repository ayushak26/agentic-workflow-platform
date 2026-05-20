FROM python:3.11-slim

# Build-time flag: include dev tooling (pytest, ruff, mypy)?
# Default 0 = lean production image
# docker-compose.yml sets this to 1 for the dev container
ARG INSTALL_DEV=0

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /code

# Install Python deps. Conditional on INSTALL_DEV.
COPY pyproject.toml ./
RUN if [ "$INSTALL_DEV" = "1" ]; then \
        pip install --no-cache-dir -e ".[dev]"; \
    else \
        pip install --no-cache-dir -e .; \
    fi

COPY app ./app
COPY workflows ./workflows

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]