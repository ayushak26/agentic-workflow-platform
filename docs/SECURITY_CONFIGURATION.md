# Security configuration

## Ordinary CI

`.github/workflows/ci.yml` never receives OpenAI or Anthropic credentials.
Its MongoDB, Weaviate, MinIO, Redis, JWT, and Grafana values are disposable
credentials for isolated GitHub runner containers. They are not production
secrets and must never be reused outside CI.

The workflow has read-only repository permission and checkout does not persist
the GitHub token in the local Git configuration.

## Live provider tests

Live calls are isolated in `.github/workflows/live-llm-tests.yml`. The workflow:

- can only be started manually;
- only runs against `main`;
- uses the protected GitHub environment `live-llm-tests`;
- gives each provider key only to the step that needs it; and
- does not cache dependencies in jobs that receive provider credentials.

Create `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` as environment secrets, not
repository variables. Add an environment approval rule and use separate
provider projects with low spending limits.

## Local development

Copy `.env.example` to `.env`, replace every `replace-with-...` value, and keep
the file uncommitted. Docker binds service ports to `127.0.0.1`, requires
authentication for Weaviate and Redis, and disables anonymous Grafana access.

The compose file is a local/development topology. Do not publish its database,
object-store, vector-store, Redis, Grafana, or Prometheus ports on a production
host.

## Production startup gate

`app.config.Settings` refuses to start when `ENVIRONMENT=production` and any of
these conditions is unsafe:

- JWT secret is a known default or shorter than 32 bytes;
- development login bypass is enabled;
- interactive API documentation is enabled;
- rate limiting, guardrails, or the semantic cache is disabled;
- MongoDB or MinIO uses a committed development password;
- Redis has no password;
- Weaviate has no API key;
- CORS contains wildcard, localhost, HTTP, or no origin; or
- trusted hosts contains wildcard or a test host.
- no LLM provider is configured, or the semantic cache has no OpenAI key.

Recommended production values:

```env
ENVIRONMENT=production
SECRET_KEY=<unique random value of at least 32 bytes>
DEV_BYPASS_ENABLED=false
API_DOCS_ENABLED=false
METRICS_ENABLED=true
CORS_ALLOWED_ORIGINS=https://ai.example.com
TRUSTED_HOSTS=ai.example.com
MONGO_URI=mongodb://<restricted-app-user>:<secret>@mongo:27017/?authSource=<db>
WEAVIATE_API_KEY=<secret>
MINIO_ACCESS_KEY=<restricted-service-account>
MINIO_SECRET_KEY=<secret>
REDIS_URL=redis://:<secret>@redis:6379/0
RATE_LIMIT_ENABLED=true
GUARDRAILS_ENABLED=true
SEMANTIC_CACHE_ENABLED=true
OPENAI_API_KEY=<server-side provider key>
```

Use a restricted MongoDB application user and a bucket-scoped MinIO service
account. The supplied IONOS topology keeps all four data services on an
internal Docker network; only Caddy publishes ports 80/443. Prometheus and
Grafana bind to VPS loopback and Caddy blocks `/metrics`.

Generate `.env.production` with `scripts/generate_production_env.py`, keep it
mode `0600` at `/opt/eurskem/shared/.env.production`, and never put production
provider or infrastructure secrets in GitHub Actions.

## GitHub repository settings

Code cannot enforce these controls, so configure them in GitHub:

1. Protect `main` and require the CI checks.
2. Require approval from Code Owners.
3. Prevent force pushes and branch deletion.
4. Enable secret scanning and push protection.
5. Enable Dependabot alerts and security updates.
6. Restrict allowed Actions to trusted publishers and full commit SHAs.
7. Never make repository secrets available to fork pull requests.
