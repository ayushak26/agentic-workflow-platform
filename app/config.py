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
    access_token_expire_minutes: int = 60
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

    weaviate_host: str = "weaviate"
    weaviate_port: int = 8080
    weaviate_grpc_port: int = 50051
    weaviate_api_key: str = ""

    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "eurskemadmin"
    minio_secret_key: str = "eurskempassword"
    minio_bucket: str = "eurskem-ai-docs"
    workflow_file_max_mb: int = 50
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

    # Scientific Agent Skills are instruction assets, not an MCP server. Only
    # explicitly approved skills can be loaded into workflow prompts.
    scientific_skills_enabled: bool = False
    scientific_skills_path: str = (
        "/opt/scientific-agent-skills/skills"
    )
    scientific_skills_allowlist: str = (
        "literature-review,scientific-writing,research-grants,"
        "scientific-critical-thinking"
    )
    scientific_skills_max_prompt_chars: int = 30000

    # SSE is the authenticated, one-way run-event transport.
    sse_heartbeat_seconds: float = 15.0
    sse_replay_events_per_run: int = 1000
    sse_replay_run_limit: int = 1000

    anthropic_api_key: str = ""
    openai_api_key: str = ""

    # Every outbound call has a finite deadline.
    external_request_timeout_seconds: float = Field(default=30.0, gt=0, le=600)
    llm_request_timeout_seconds: float = Field(default=120.0, gt=0, le=900)

    # Tenant-scoped semantic cache for deterministic plain-text completions.
    semantic_cache_enabled: bool = False
    semantic_cache_similarity_threshold: float = Field(default=0.97, ge=0.80, le=1.0)
    semantic_cache_ttl_seconds: int = Field(default=3_600, ge=60, le=604_800)
    semantic_cache_max_entries_per_scope: int = Field(default=200, ge=10, le=5_000)

    # Guardrails applied before workflow execution and after every node.
    guardrails_enabled: bool = True
    guardrail_pii_mode: Literal["audit", "redact", "block"] = "audit"
    guardrail_max_text_chars: int = Field(default=2_000_000, ge=1_000, le=10_000_000)

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

    # LLM resilience is owned by the provider-neutral registry. Provider SDK
    # retries are disabled so one policy controls attempt count, backoff,
    # failover, metrics, and logs without multiplying hidden SDK retries.
    llm_retry_attempts: int = 3
    llm_retry_base_delay_seconds: float = 1.0
    llm_retry_max_delay_seconds: float = 8.0
    llm_retry_jitter_ratio: float = 0.2

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
