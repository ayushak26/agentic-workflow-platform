# Eurskem AI

Agentic workflow platform with durable human approval gates, proposal
workflows, evidence retrieval, provider failover, cost accounting, and a
production IONOS deployment.

## Local development

Docker Compose now has isolated development defaults, so a missing `.env` no
longer causes interpolation errors:

```bash
docker compose build
docker compose up -d
```

Copy `.env.example` to `.env` only when you need custom local credentials or
provider keys. Never reuse the development defaults in production.

## Verification

```bash
uv sync --frozen --all-extras --dev
.venv/bin/python -m pytest -q
.venv/bin/python scripts/preflight_workflows.py --warnings-as-errors
cd ui && npm ci && npm run lint -- --max-warnings=0 && npm run build
```

## Production

- [IONOS deployment guide](docs/IONOS_PRODUCTION_DEPLOYMENT.md)
- [Production readiness evidence](docs/PRODUCTION_READINESS_MATRIX.md)
- [Security configuration](docs/SECURITY_CONFIGURATION.md)
- [Runtime readiness implementation](RUNTIME_READINESS_IMPLEMENTATION.md)

Start from `.env.production.example` or generate a complete secret file with
`scripts/generate_production_env.py`. The production startup gate refuses
development credentials, unsafe origins, missing rate limits/guardrails/cache,
or missing provider configuration.
