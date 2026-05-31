# Security Architecture

## 1. IP Protection

All agent prompts and workflow logic live in YAML files on the server.
The API never returns prompt text in any response — only generated output.
Node IDs in API responses are abstract identifiers (e.g. `rfp_intel`, `compile_qa`).
The `/api/node-types` endpoint (which returns node configs) requires `admin` role.
Non-admin tokens cannot retrieve agent definitions via any API route.

Residual risk: a sophisticated user might reverse-engineer prompt intent from outputs.
Mitigation: audit logging flags unusual query patterns; RBAC limits config access.

## 2. Microsoft SSO

**Design**: Standard OAuth 2.0 Authorization Code Grant via Azure AD OIDC.

**Stub**: `app/security/sso_stub.py` issues a locally-signed JWT with the same
claim structure (`sub`, `role`, `session_id`, `exp`) that real Azure AD would produce.
Swapping in real SSO requires two environment variables (`AZURE_TENANT_ID`, `AZURE_CLIENT_ID`)
and replacing the body of the `/auth/token` handler with the MSAL library callback.
Zero other code changes needed.

**Real configuration requires from Eurskem IT (one-time, ~2 hours)**:
- Register Eurskem app in Azure AD portal
- Provide: Tenant ID, Client ID, Client Secret, authorized redirect URIs
- Grant permissions: User.Read, profile, email

## 3. Pigeon Holes (Session-Level Data Isolation)

The SoW requires that Consultant A's proposal data is NEVER visible in Consultant B's session.
This is enforced at four independent layers:

### 3a. Vector Store (Weaviate)
Every `hybrid_search` call ANDs `session_id == caller_session_id` into the Weaviate filter
before the query executes. The retrieval module never exposes a filter-free search path.

### 3b. Workflow State (LangGraph)
`WorkflowState` carries `session_id` as a field. LangGraph thread_id = `run_id` (not `session_id`),
which means the checkpointer never conflates two runs in the same session.
`session_id` is set at workflow start and is read-only throughout execution.

### 3c. Cache (Redis)
Cache keys are namespaced: `cache:{session_id}:{query_hash}`.
A session B query will never match a session A cache key because the prefixes differ.
Cache entries expire on session completion (TTL = 24h).

### 3d. Audit Log (MongoDB)
Every audit event includes `session_id`. Audit queries always filter by `session_id`.
The isolation verifier tests that a session B audit query returns zero session A events.

### Isolation Verifier
`tests/test_isolation_verifier.py` runs four test classes covering all layers above.
Run against live Docker stack: `pytest tests/test_isolation_verifier.py -v -m integration`

## 4. JWT and RBAC

Three roles: `viewer` (read-only), `consultant` (run workflows), `admin` (full config access).
Every API route declares its minimum role via `Depends(require_role('...'))`.
The JWT contains `sub`, `role`, `session_id`, `exp`, `iat`.
Tokens are HS256-signed locally in stub mode; in production, sign with RSA-256 private key
stored in Azure Key Vault / AWS Secrets Manager.

## 5. Encryption (Documented, Not Implemented in Local Stack)

- TLS 1.3: Handled by the load balancer (ALB on AWS, Application Gateway on Azure).
  In local Docker Compose this is HTTP; adding nginx with self-signed cert is one-liner.
- AES-256 at rest: S3 SSE-S3 or SSE-KMS on AWS; equivalent on Azure Blob / GCS.
  MinIO supports server-side encryption in the same protocol.