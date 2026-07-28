# Production readiness evidence

This matrix maps every requirement from the supplied production checklist to
the implemented code and its verification gate.

| # | Requirement | Implementation evidence | Verification |
|---:|---|---|---|
| 1 | API keys in environment variables | `app/config.py`, `.env.production.example`, generated mode-0600 server environment | `scripts/production_preflight.py`, `tests/test_security_config.py` |
| 2 | Per-user rate limiting | Redis fixed-window limiter keyed by verified JWT subject; IP fallback for login | `tests/test_production_controls.py` |
| 3 | Input guardrails | Prompt-injection blocking, configurable PII audit/redact/block, text-size cap | `tests/test_production_controls.py` |
| 4 | Output guardrails | Recursive API-key, assigned-secret and private-key redaction after each node | `tests/test_production_controls.py` |
| 5 | Semantic cache | Redis cache scoped by tenant, model, system prompt and generation settings; exact and semantic matches | `tests/test_production_controls.py`, `/ready` cache probe |
| 6 | Streaming | Plain-text provider chunks and workflow lifecycle events stream through Redis-backed WebSockets or authenticated SSE with keepalives; structured/tool calls stay atomic | token-event test, frontend build/lint, run-ID reservation and ownership checks |
| 7 | Exponential backoff | One provider-neutral retry policy with exponential delay, jitter and `Retry-After` handling | `tests/test_llm_resilience.py` |
| 8 | Fallback chain | Configured-provider detection and Claude/OpenAI cross-provider fallback | `tests/test_llm_resilience.py`, manual live-provider workflow |
| 9 | Structured logs and request IDs | Structlog context variables, accepted/generated request IDs, HTTP/node/provider event fields | `tests/test_production_controls.py` |
| 10 | Cost per request/user | Run, node, model, token, cache-hit and tenant cost ledger; UTC daily spend checks | cost API and existing ledger tests |
| 11 | Dependency health | Concurrent Mongo, Weaviate, MinIO, Redis, checkpointer, cache and MCP probes | `tests/test_runtime_readiness.py`, public deployment smoke test |
| 12 | Maximum tokens | Pre-call input estimate, output clamp and emergency-mode input ceiling | registry tests and production settings validation |
| 13 | External timeouts | Mongo, Redis, MinIO, Weaviate, embeddings, providers and MCP have finite deadlines | configuration tests and code-level deadlines |
| 14 | Production CORS | Explicit HTTPS origin and trusted-host startup validation; Caddy same-origin frontend | `tests/test_security_config.py` |
| 15 | 100 concurrent users | Authenticated, no-LLM release load gate with 100 unique users and a p95 threshold | `scripts/load_test.py`, executed by `deploy_release.sh` |

Additional release gates include Argon2 local users, signed JWT claims,
tenant-scoped files/runs/audit/cost/events, Redis-backed restart-safe HITL,
read-only application filesystem, non-root container execution, internal-only
data services, automatic TLS, CI-before-deploy, immutable release checksums,
automatic rollback, and production backup tooling.
