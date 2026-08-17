from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        extra="ignore",
    )

    secret_key: str = "insecure-dev-secret-change-me"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 600
    jwt_issuer: str = "eurskem-ai"
    jwt_audience: str = "eurskem-ai-ui"
    environment: str = "development"
    api_docs_enabled: bool = True
    metrics_enabled: bool = True
    cors_allowed_origins: str = (
        "http://localhost:5173,http://localhost:3000"
    )
    trusted_hosts: str = "localhost,127.0.0.1,testserver"

    mongo_uri: str = "mongodb://eurskem:eurschempass@localhost:27017"
    mongo_db: str = "eurskem_ai"

    retrieval_reranker_model: str = "claude-sonnet-4-5"
    retrieval_compressor_model: str = "claude-sonnet-4-5"
    retrieval_trace_retention_days: int = 30

    weaviate_host: str = "weaviate"
    weaviate_port: int = 8080
    weaviate_grpc_port: int = 50051
    weaviate_api_key: str = ""

    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "eurskemadmin"
    minio_secret_key: str = "eurskempassword"
    minio_bucket: str = "eurskem-ai-docs"
    workflow_file_max_mb: int = 70
    workflow_file_max_files: int = 20
    workflow_file_max_total_mb: int = 200
    max_request_body_mb: int = 220

    @property
    def workflow_file_max_bytes(self) -> int:
        return self.workflow_file_max_mb * 1024 * 1024

    @property
    def workflow_file_max_total_bytes(self) -> int:
        return self.workflow_file_max_total_mb * 1024 * 1024

    @property
    def max_request_body_bytes(self) -> int:
        return self.max_request_body_mb * 1024 * 1024

    redis_url: str = "redis://localhost:6379/0"
    health_probe_timeout_seconds: float = 2.0
    readiness_required_services: str = (
        "mongo,weaviate,minio,redis,checkpointer,mcp:eurskem"
    )

    # The Docker image installs paper-search-mcp as a pinned Python package.
    # A source checkout path remains optional for local upstream development.
    paper_search_mcp_enabled: bool = False
    paper_search_mcp_path: str = ""
    paper_search_mcp_command: str = "python"
    paper_search_mcp_module: str = "paper_search_mcp.server"
    mcp_startup_timeout_seconds: float = 30.0
    mcp_tool_timeout_seconds: float = 90.0

    # ── Dynamics 365 CRM, exposed through MCP ────────────────────────────────
    # The workflow references the server by id; these credentials are handed to
    # the MCP subprocess and never appear in workflow YAML, the Builder, or a
    # run record. `mock` is the default so a demo or a fresh checkout works
    # without a tenant, and a misconfigured live connection surfaces as an
    # obvious demo backend rather than a half-working production one.
    dynamics_mcp_enabled: bool = True
    dynamics_mcp_mode: str = "mock"        # "mock" | "live"
    dynamics_url: str = ""                 # https://your-org.crm.dynamics.com
    dynamics_tenant_id: str = ""
    dynamics_client_id: str = ""
    dynamics_client_secret: str = ""
    dynamics_fixtures_path: str = ""       # overrides the bundled demo fixtures

    # ── Dynamics 365 Finance & Supply Chain (F&O OData), exposed through MCP ──
    # A distinct product from the Dataverse CRM above: F&O customers, sales
    # orders and inventory rather than CRM accounts/opportunities. `live` mode
    # runs the real server at mcp-servers/d365-finance-scm-mcp (Node/TypeScript,
    # requires `npm run build` + a real F&O environment). `mock` mode (the
    # default) runs app/mcp/d365_finance/server.py, a fixture-backed Python
    # server exposing the narrow business tools (find_customer,
    # find_account_ownership, find_credit_status, find_quote,
    # find_sales_order, find_order_fulfilment_status,
    # find_inventory_availability, find_installed_unit, find_shipment,
    # find_invoice, find_contract, find_products) that the live server's own
    # README recommends
    # building on top of its generic OData adapter — that business-tool layer
    # does not exist yet on the live server, so the two modes are not
    # tool-for-tool identical the way the Dataverse CRM mock/live pair is.
    # Writes/deletes are fail-closed inside the live server itself (see its
    # README) in addition to this platform's own write_policy gate.
    fno_mcp_enabled: bool = True
    fno_mcp_mode: str = "mock"              # "mock" | "live"
    fno_base_url: str = ""                 # https://your-environment.operations.dynamics.com
    fno_tenant_id: str = ""
    fno_client_id: str = ""
    fno_client_secret: str = ""
    fno_allow_writes: bool = False
    fno_allow_deletes: bool = False
    fno_read_entity_allowlist: str = ""
    fno_write_entity_allowlist: str = ""
    fno_delete_entity_allowlist: str = ""
    fno_entity_aliases_json: str = ""

    # ── Business Records (MySQL), exposed through MCP ────────────────────────
    # A real, live MySQL database — unlike the Dataverse CRM / F&O connections
    # above (fixture-backed in-memory mocks reloaded from JSON on every process
    # start), this one persists for real. Seeded once from the same two
    # fixture files (app/mcp/dynamics/fixtures.json,
    # app/mcp/d365_finance/fixtures.json) via schema.sql + seed.py — see
    # app/mcp/business_records/. Exposes narrow, classified tools
    # (customer_search/order_search/inventory_check/product_search — read;
    # create_case/create_opportunity/create_order/update_order/update_case —
    # write) rather than a raw SQL executor.
    business_records_mcp_enabled: bool = True
    business_records_mysql_host: str = "127.0.0.1"
    business_records_mysql_port: int = 3306
    business_records_mysql_user: str = "eurskem-app"
    business_records_mysql_password: str = "eurskem-local-dev"
    business_records_mysql_database: str = "business_records"
    # A second, genuinely lower-privileged credential for the query_readonly
    # MCP tool (SQLQueryAgent) — GRANT SELECT only, created by seed.py's
    # ensure_readonly_user(). The only layer of the tool's defense-in-depth
    # that holds on its own: the existing business_records_mysql_user above
    # has ALL PRIVILEGES, so a SQL-injection-proof query string alone would
    # still be one bug away from a write if it ran under that account.
    business_records_readonly_mysql_user: str = "eurskem-app-ro"
    business_records_readonly_mysql_password: str = "eurskem-local-dev-ro"
    # Only ever used once, by seed.py's ensure_readonly_user() — the regular
    # app user has no CREATE USER privilege, by design, so provisioning the
    # read-only account needs root. Matches docker-compose.yml's own
    # BUSINESS_RECORDS_MYSQL_ROOT_PASSWORD env var / local-dev default.
    business_records_mysql_root_password: str = "eurskem-local-dev-root"

    # Email OAuth (Outlook via Microsoft Graph, Gmail) — lets someone connect
    # a real mailbox through the Builder instead of a deployment operator
    # hand-editing EMAIL_CONNECTIONS with a static, non-refreshing access
    # token (see app/integrations/email/__init__.py). Registering the actual
    # Azure AD app (Graph Mail.Send/Mail.ReadWrite delegated scopes) and
    # Google Cloud OAuth client (Gmail gmail.send/gmail.compose/
    # gmail.readonly scopes) is an external, deployment-owner prerequisite —
    # these settings only hold what such an app registration issues.
    microsoft_oauth_client_id: str = ""
    microsoft_oauth_client_secret: str = ""
    microsoft_oauth_tenant_id: str = "common"
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    #: Where a provider redirects back to after consent — this app's own
    #: externally-reachable origin, e.g. https://app.example.com. The full
    #: callback path (/email/oauth/callback/{provider}) is appended by
    #: app/api/email_oauth.py.
    oauth_redirect_base_url: str = "http://localhost:8000"
    #: Root key (envelope-encryption KEK) for the OAuth token vault
    #: (app/integrations/email/token_vault.py). Deliberately separate from
    #: both secret_key (JWT signing) and entity_vault_master_key (a
    #: different security domain) — the same key-separation reasoning as
    #: entity_vault_master_key above: a leaked key must compromise exactly
    #: one thing, never a second by coincidence.
    email_token_vault_master_key: str = ""

    # Research API credentials for paper-search-mcp. These never reach the
    # subprocess by ambient inheritance in local dev (pydantic-settings' own
    # env_file loading does not populate os.environ) — app/mcp/client.py
    # passes them explicitly into the launched process's environment, using
    # the exact names paper_search_mcp.config.get_env() looks for.
    paper_search_mcp_openalex_api_key: str = ""
    paper_search_mcp_unpaywall_email: str = ""
    paper_search_mcp_core_api_key: str = ""
    paper_search_mcp_semantic_scholar_api_key: str = ""

    # Scientific Agent Skills are instruction assets, not an MCP server. Only
    # explicitly approved skills can be loaded into workflow prompts.
    scientific_skills_enabled: bool = False
    scientific_skills_path: str = (
        "scientific-agent-skills/skills"
    )
    scientific_skills_allowlist: str = (
        "literature-review,scientific-writing,research-grants,"
        "scientific-critical-thinking,research-lookup,database-lookup,"
        "scientific-brainstorming,peer-review,geomaster,geopandas,pymoo,"
        "networkx,hypothesis-generation,statistical-analysis"
    )
    scientific_skills_max_prompt_chars: int = 30000

    # Bounded Deep Research: a chat_with_tools loop over the generic LLM
    # gateway plus the web_search service — no dedicated provider endpoint.
    deep_research_enabled: bool = False

    # SSE is the authenticated, one-way run-event transport.
    sse_heartbeat_seconds: float = 15.0
    sse_replay_events_per_run: int = 1000
    sse_replay_run_limit: int = 1000
    # How long a run's Redis replay stream survives after its last event, so a
    # reconnecting client can still resume from its Last-Event-ID.
    sse_replay_ttl_seconds: int = Field(default=86_400, ge=60)

    anthropic_api_key: str = ""
    openai_api_key: str = ""
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # Timeouts specific to OpenRouter's own catalog/generation endpoints
    # (app/llm/openrouter_gw.py, app/llm/openrouter_catalog.py) — separate
    # from llm_request_timeout_seconds since these are metadata calls, not
    # generations.
    openrouter_request_timeout_seconds: float = 15.0

    # Presidio Analyzer — the PII/entity detection safety-net tier for
    # app/security/entity_tokenizer.py (replaces the former in-process spaCy NER pass).
    presidio_analyzer_url: str = "http://localhost:5001"
    presidio_request_timeout_seconds: float = Field(default=10.0, gt=0, le=60)

    # Ephemeral cache_control TTL applied to every AnthropicGateway request
    # (system prompt + tools + the growing multi-turn message history in
    # chat_with_tools). "1h" costs a 2x cache-write premium vs 5m's 1.25x,
    # but survives gaps between LLM calls within a long-running proposal
    # workflow -- worth it once turns are more than ~5 minutes apart.
    anthropic_prompt_cache_ttl: Literal["5m", "1h"] = "1h"

    # Live web search: Tavily (dedicated search API), OpenAI (Responses API
    # web_search tool), or Kimi/Moonshot ($web_search builtin function).
    # "auto" tries them in that order, per whichever credentials are set —
    # see WebSearchService._select_provider in app/tools/web_io.py.
    web_search_backend: Literal["auto", "tavily", "openai", "kimi", "stub"] = "auto"
    tavily_api_key: str = ""
    openai_web_search_model: str = "gpt-5"
    web_search_max_tool_rounds: int = Field(default=4, ge=1, le=10)
    kimi_web_search_model: str = "kimi-k3"
    # Kimi web search and vision authenticate with the same Moonshot account
    # as the local-model LLM gateway — see the moonshot_api_key and
    # kimi_api_base_url properties near the bottom of this class, which
    # alias local_kimi_api_key/local_kimi_base_url rather than duplicating
    # that credential under a second setting name.

    # OpenAI image generation (app/tools/image_io.py).
    image_generation_backend: Literal["disabled", "openai"] = "openai"
    openai_image_model: str = "gpt-image-2-2026-04-21"

    # Kimi K3 vision / image understanding (app/tools/vision_io.py).
    kimi_vision_model: str = "kimi-k3"
    kimi_vision_max_image_bytes: int = Field(
        default=20 * 1024 * 1024, ge=1024,
    )

    # Every outbound call has a finite deadline.
    external_request_timeout_seconds: float = Field(default=30.0, gt=0, le=600)

    # SubprocessAgent: how many levels a subprocess chain may nest at
    # runtime before it is refused — a real static cycle is already caught
    # by preflight, but two workflows can be made mutually recursive after
    # the fact (or the child might not exist yet at authoring time), so this
    # is the runtime backstop.
    subprocess_max_depth: int = Field(default=3, ge=1, le=10)

    # PythonSnippetAgent's isolated executor (app/runtime/snippet_daemon.py),
    # reached over a Unix socket shared with the network-isolated sidecar via
    # a volume — never a TCP port, since that sidecar has network_mode: none.
    snippet_runner_enabled: bool = True
    snippet_runner_socket_path: str = "/run/snippet-runner/snippet-runner.sock"
    snippet_default_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    snippet_default_memory_mb: int = Field(default=128, ge=16, le=1024)

    llm_request_timeout_seconds: float = Field(default=200.0, gt=0, le=900)
    # Strict preflight checks provider model metadata, never generation.
    llm_model_access_probe_timeout_seconds: float = Field(
        default=8.0,
        gt=0,
        le=60,
    )
    llm_model_access_cache_ttl_seconds: int = Field(
        default=300,
        ge=30,
        le=3600,
    )

    # Tenant-scoped semantic cache for deterministic plain-text completions.
    semantic_cache_enabled: bool = False
    semantic_cache_similarity_threshold: float = Field(default=0.97, ge=0.80, le=1.0)
    semantic_cache_ttl_seconds: int = Field(default=3_600, ge=60, le=604_800)
    semantic_cache_max_entries_per_scope: int = Field(default=200, ge=10, le=5_000)

    # Guardrails applied before workflow execution and after every node.
    guardrails_enabled: bool = True
    guardrail_pii_mode: Literal["audit", "redact", "block"] = "audit"
    guardrail_max_text_chars: int = Field(default=2_000_000, ge=1_000, le=10_000_000)

    # Confidential Entity Protection / pre-LLM pseudonymisation (Phase 1).
    # "pseudonymised" (the default) replaces registered/detected entities with
    # stable placeholders before any external LLM call; "public" is a no-op
    # passthrough; "restricted_local" is not implemented yet (falls back to
    # pseudonymised — see app/security/entity_tokenizer.py).
    entity_protection_default_mode: Literal[
        "public", "pseudonymised", "restricted_local"
    ] = "pseudonymised"
    entity_mapping_ttl_seconds: int = Field(default=30 * 86_400, ge=3_600)
    # Root key (envelope-encryption KEK) for the entity mapping vault.
    # Deliberately separate from secret_key (JWT signing) — a leaked JWT
    # secret must not also decrypt every protected entity ever tokenized.
    # Required (checked lazily on first real use, see entity_vault.py) once
    # entity_protection_default_mode != "public".
    entity_vault_master_key: str = ""

    # Redis-backed fixed-window rate limits across Uvicorn workers.
    rate_limit_enabled: bool = True
    rate_limit_requests_per_minute: int = Field(default=60, ge=1, le=10_000)
    rate_limit_auth_requests_per_minute: int = Field(default=10, ge=1, le=1_000)

    # Optional OpenTelemetry export.
    otel_enabled: bool = False
    otel_service_name: str = "eurskem-ai"
    otel_exporter_otlp_endpoint: str = ""

    # Private OpenAI-compatible inference endpoints. These URLs are deployment
    # configuration, never workflow input, which prevents per-run SSRF.
    local_kimi_enabled: bool = False
    local_kimi_base_url: str = "http://host.docker.internal:8101/v1"
    local_kimi_api_key: str = ""
    local_kimi_served_model: str = "kimi-k3"
    local_kimi_reasoning_effort: Literal["low", "high", "max"] = "max"

    local_glm_enabled: bool = False
    local_glm_base_url: str = "http://host.docker.internal:8102/v1"
    local_glm_api_key: str = ""
    local_glm_served_model: str = "glm-5"
    local_glm_reasoning_effort: Literal["high", "max"] = "max"
    local_glm_enable_thinking: bool = True

    local_llm_timeout_seconds: float = 600.0
    local_llm_verify_served_model: bool = True
    local_llm_readiness_required: bool = False

    # A separate OpenAI-compatible embedding server can keep RAG fully local.
    # Kimi K3 and GLM-5 are generation models, not embedding models.
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536

    # Vision-augmented PDF parsing: renders pages carrying figures, charts and
    # image-only tables and transcribes them with a vision model, so visual
    # content becomes searchable. Opt-in per Parser Profile
    # (strategy: vision_augmented); these settings bound provider and cost.
    ingestion_vision_provider: Literal["openrouter", "kimi"] = "openrouter"
    ingestion_vision_model: str = ""  # blank -> provider default
    ingestion_vision_max_pages: int = Field(default=20, ge=0, le=500)
    ingestion_vision_scale: float = Field(default=2.0, ge=0.5, le=6.0)
    ingestion_vision_concurrency: int = Field(default=3, ge=1, le=16)
    ingestion_vision_max_output_tokens: int = Field(default=4096, ge=256, le=32_768)

    # LLM resilience is owned by the provider-neutral registry. Provider SDK
    # retries are disabled so one policy controls attempt count, backoff,
    # failover, metrics, and logs without multiplying hidden SDK retries.
    llm_retry_attempts: int = 3
    llm_retry_base_delay_seconds: float = 1.0
    llm_retry_max_delay_seconds: float = 8.0
    llm_retry_jitter_ratio: float = 0.2

    # A run stuck in "running" with no write activity for this long (e.g. the
    # server process restarted or crashed mid-execution) is treated as
    # orphaned and lazily flipped to "failed" the next time it is read.
    stale_run_after_seconds: int = Field(default=900, ge=60, le=86_400)

    # Deleting a still-"running" run is blocked until it's been running at
    # least this long — a run that's only been going for a minute is almost
    # certainly still legitimately in progress, not abandoned. Paused,
    # completed, failed, and rejected runs have no such restriction.
    run_delete_min_running_age_seconds: int = Field(default=86_400, ge=0)

    # A background sweep (see cleanup_stale_runs in app/workflow/run_history.py)
    # periodically hard-deletes runs stuck in "running" or "paused" for at
    # least this long. A "paused" run is deleted by age alone — nothing owns
    # a paused run once its process has parked it awaiting resume/HITL. A
    # "running" run is deleted only if it's ALSO confirmed orphaned (its
    # owner_pid is dead) so a genuinely still-executing long job is never
    # touched.
    run_auto_cleanup_after_seconds: int = Field(default=86_400, ge=60)
    run_auto_cleanup_interval_seconds: int = Field(default=3_600, ge=60)

    # TTL of the Redis leases that make cross-worker ownership exclusive — the
    # stale-run cleanup leader and BackgroundRunManager's per-run launch lease.
    # Both renew while they hold it, so this only bounds how long a dead
    # worker's claim blocks another worker from taking over.
    distributed_lease_seconds: int = Field(default=120, ge=30)

    dev_bypass_enabled: bool = True
    dev_bypass_username: str = "ayush"
    dev_bypass_password: str = "dev123"

    @model_validator(mode="after")
    def validate_production_security(self) -> "Settings":
        """Refuse to boot production with development security settings."""

        local_problems: list[str] = []
        for enabled, name, url, served_model in (
            (
                self.local_kimi_enabled,
                "LOCAL_KIMI",
                self.local_kimi_base_url,
                self.local_kimi_served_model,
            ),
            (
                self.local_glm_enabled,
                "LOCAL_GLM",
                self.local_glm_base_url,
                self.local_glm_served_model,
            ),
        ):
            if not enabled:
                continue
            if not _valid_http_url(url):
                local_problems.append(
                    f"{name}_BASE_URL must be an http(s) URL without credentials"
                )
            if not served_model.strip():
                local_problems.append(f"{name}_SERVED_MODEL cannot be empty")
        if self.local_llm_timeout_seconds <= 0:
            local_problems.append("LOCAL_LLM_TIMEOUT_SECONDS must be positive")
        if self.embedding_base_url and not _valid_http_url(
            self.embedding_base_url
        ):
            local_problems.append(
                "EMBEDDING_BASE_URL must be an http(s) URL without credentials"
            )
        if self.embedding_dimensions <= 0:
            local_problems.append("EMBEDDING_DIMENSIONS must be positive")
        if local_problems:
            raise ValueError(
                "Invalid local model configuration: "
                + "; ".join(local_problems)
            )

        if self.environment.strip().lower() != "production":
            return self

        problems: list[str] = []
        insecure_secret_keys = {
            "",
            "change-me-in-production-32-chars-min",
            "insecure-dev-secret-change-me",
            "replace-with-a-unique-random-secret",
        }
        if (
            self.secret_key in insecure_secret_keys
            or _is_placeholder(self.secret_key)
            or len(self.secret_key.encode("utf-8")) < 32
        ):
            problems.append("SECRET_KEY must be a unique value of at least 32 bytes")
        if self.dev_bypass_enabled:
            problems.append("DEV_BYPASS_ENABLED must be false")
        if self.api_docs_enabled:
            problems.append("API_DOCS_ENABLED must be false")
        if (
            not self.weaviate_api_key.strip()
            or _is_placeholder(self.weaviate_api_key)
        ):
            problems.append("WEAVIATE_API_KEY must be configured")
        if (
            "eurschempass" in self.mongo_uri
            or _is_placeholder(self.mongo_uri)
        ):
            problems.append("MONGO_URI must not use the committed development password")
        if (
            self.minio_secret_key in {"", "eurskempassword"}
            or _is_placeholder(self.minio_secret_key)
        ):
            problems.append(
                "MINIO_SECRET_KEY must not use the committed development password"
            )
        if (
            not _redis_url_has_password(self.redis_url)
            or _is_placeholder(self.redis_url)
        ):
            problems.append("REDIS_URL must include authentication")
        if not self.semantic_cache_enabled:
            problems.append("SEMANTIC_CACHE_ENABLED must be true in production")
        if not self.rate_limit_enabled:
            problems.append("RATE_LIMIT_ENABLED must be true in production")
        if not self.guardrails_enabled:
            problems.append("GUARDRAILS_ENABLED must be true in production")
            
        origins = self.allowed_cors_origins
        if not origins:
            problems.append("CORS_ALLOWED_ORIGINS must contain an HTTPS origin")
        elif any(
            origin == "*"
            or "localhost" in origin
            or "127.0.0.1" in origin
            or not origin.startswith("https://")
            for origin in origins
        ):
            problems.append(
                "CORS_ALLOWED_ORIGINS must contain only explicit HTTPS origins"
            )

        hosts = self.allowed_hosts
        if not hosts or "*" in hosts or "testserver" in hosts:
            problems.append(
                "TRUSTED_HOSTS must contain explicit production hostnames"
            )

        if problems:
            raise ValueError(
                "Unsafe production configuration: " + "; ".join(problems)
            )
        return self

    @property
    def moonshot_api_key(self) -> str:
        """Kimi web search and vision (app/tools/web_io.py, vision_io.py)
        authenticate as the same Moonshot account as the LLM-gateway route —
        one credential, LOCAL_KIMI_API_KEY, not a second parallel setting."""
        return self.local_kimi_api_key

    @property
    def kimi_api_base_url(self) -> str:
        return self.local_kimi_base_url

    @property
    def required_readiness_services(self) -> tuple[str, ...]:
        return tuple(
            name.strip()
            for name in self.readiness_required_services.split(",")
            if name.strip()
        )

    @property
    def resolved_paper_search_mcp_path(self) -> Path | None:
        value = self.paper_search_mcp_path.strip()
        return Path(value).expanduser() if value else None

    @property
    def resolved_scientific_skills_path(self) -> Path:
        return Path(self.scientific_skills_path).expanduser()

    @property
    def allowed_scientific_skills(self) -> tuple[str, ...]:
        return _csv_values(self.scientific_skills_allowlist)

    @property
    def allowed_cors_origins(self) -> tuple[str, ...]:
        return _csv_values(self.cors_allowed_origins)

    @property
    def allowed_hosts(self) -> tuple[str, ...]:
        return _csv_values(self.trusted_hosts)


def _csv_values(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _redis_url_has_password(url: str) -> bool:
    """Return whether a Redis URL contains a non-empty password component."""

    from urllib.parse import urlsplit

    try:
        return bool(urlsplit(url).password)
    except ValueError:
        return False


def _valid_http_url(value: str) -> bool:
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return False
    return bool(
        parsed.scheme in {"http", "https"}
        and parsed.hostname
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment
    )


def _is_placeholder(value: str) -> bool:
    normalized = value.strip().lower()
    return "replace-with" in normalized or "change-me" in normalized


settings = Settings()
