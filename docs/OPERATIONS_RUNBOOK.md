# Eurskem AI — Operations Runbook

Operational notes for running the platform in production (IONOS VPS,
docker-compose.production.yml, Caddy edge). Pairs with
`deploy/ionos/{setup_host.sh,deploy_release.sh,backup.sh,restore.sh}` and
`observability/prometheus/alerts.yml`.

---

## 1. Rate limiting fails CLOSED when Redis is down (deliberate)

`RedisRateLimitMiddleware` (app/security/middleware.py) returns **503 +
Retry-After** for `/api/*` and `/auth/*` when Redis is unreachable **in
production** (in development it degrades open, so a bare checkout still
works).

**Tradeoff:** a Redis outage becomes a full API outage instead of an
unthrottled API. This is the chosen posture because the platform's
authentication surface (credential stuffing) and LLM cost surface
(run-launch endpoints) are both protected only while the limiter is live;
an "open" failure mode under attack is the worse incident.

**Operator response to `Rate-limit service is unavailable` 503s:**

1. Check Redis: `docker compose -f docker-compose.production.yml ps redis`
   and its logs (AOF persistence is on: `appendonly yes`,
   `appendfsync everysec`).
2. Redis restart is safe: run-state coordination (run ownership leases,
   SSE replay streams) is rebuilt on demand; durable run records live in
   Mongo. LangGraph checkpoints in Redis may be lost on a hard failure —
   paused runs then resume through the Mongo replay path
   (`resume_workflow_durable` in app/runtime/hitl.py), which is slower but
   correct.
3. Do **not** "fix" the 503s by disabling the limiter in config; restore
   Redis instead.

## 2. Backup & restore

| Action | Command | Notes |
| --- | --- | --- |
| Backup | `deploy/ionos/backup.sh` | Mongo dump + MinIO mirror + Weaviate/Redis volume tars + workflows; checksummed; brief stop of caddy/app/weaviate/redis during volume capture |
| Restore | `deploy/ionos/restore.sh <archive>` | Interactive RESTORE confirmation; sha256 check; overwrites live state |

**Drill policy:** run a restore against a scratch VPS (or a local compose
stack with the production compose file) at least once per quarter and
after any change to backup.sh/restore.sh. A backup that has never been
restored is a hope, not a procedure.

**Off-server copies:** backup.sh deliberately prints that a VPS-local
backup is not sufficient — copy archives to encrypted off-server storage
(S3-compatible, another host) and keep at least the last 7 dailies.

## 3. Alerts

`observability/prometheus/alerts.yml` ships the minimum rule set (mounted
read-only into the Prometheus container):

| Alert | Meaning | First action |
| --- | --- | --- |
| `InstanceDown` (critical) | scrape failing 2 min | container health, `/health` |
| `HighApiErrorRate` (critical) | 5xx ratio > 2% / 5 min | app logs (request_id attached) |
| `ApiLatencyHigh` (warning) | p95 > 2 s / 10 min | DB / provider latency |
| `WorkflowFailureRateHigh` (warning) | > 20% failed / 15 min | Cockpit failure diagnosis, provider status |
| `LlmFailoverSurge` (warning) | fallback substitutions elevated | provider status; cost ledger intended-vs-actual |
| `RateLimitRejectionSurge` (warning) | > 1 rejection/s / 5 min | source IPs; possible attack |

Rules fire inside Prometheus. **Routing to a pager is not yet configured**
(no Alertmanager in the production compose): until it is, treat Grafana
alert panels as the on-call surface and check them on a schedule. Adding
`alertmanager` + a `route:` block is the next operational task.

## 4. Failure scenarios quick reference

| Failure | System behavior | Operator action |
| --- | --- | --- |
| Redis down | 503 on /api/*, /auth/* (fail-closed); SSE reconnects stall | restore Redis (§1) |
| Mongo down | API starts only with Mongo; running app degrades to 503s on data routes; runs already executing fail durably (records persisted on recovery) | restore Mongo; runs in `running` state are reconciled by the cleanup sweeper |
| Weaviate/MinIO down | knowledge/retrieval and file routes fail; workflow runs without those nodes continue | restore service |
| LLM provider outage | gateway retries with Retry-After, then walks the fallback chain; substitutions recorded in the cost ledger | none for short outages; check `LlmFailoverSurge` for sustained ones |
| Deploy fails readiness | `deploy_release.sh` automatically rebuilds and re-runs the previous release | read printed logs; `current` symlink only moves after readiness + smoke |
| Migration failure | boot aborts with `MigrationError` before serving traffic | fix forward; migrations are idempotent and lease-locked |
| Worker restart mid-run | background run ownership lease lapses; the sweeper marks stale `running` records; HITL resumes replay from Mongo | none normally |

## 5. Health endpoints

- `GET /health` — liveness (does not touch dependencies)
- `GET /ready` — per-service probes (mongo, weaviate, minio, redis,
  checkpointer, mcp) with per-probe latency; used by the deploy script's
  readiness gate and by load balancers
- `/metrics`, `/docs`, `/redoc`, `/openapi.json` — blocked at the edge by
  Caddy in production (`@private` matcher returns 404)
