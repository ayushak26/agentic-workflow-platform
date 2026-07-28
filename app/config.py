from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        extra="ignore",
    )

    secret_key: str = "insecure-dev-secret-change-me"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    environment: str = "development"
    api_docs_enabled: bool = True
    metrics_enabled: bool = False
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

    @property
    def workflow_file_max_bytes(self) -> int:
        return self.workflow_file_max_mb * 1024 * 1024

    redis_url: str = "redis://localhost:6379/0"
    health_probe_timeout_seconds: float = 2.0
    readiness_required_services: str = (
        "mongo,weaviate,minio,redis,checkpointer,mcp:eurskem"
    )

    # Optional external scholarly-search MCP server. Keeping this disabled and
    # pathless by default makes the application portable; deployments that use
    # EvidenceAgent enable it explicitly in their environment.
    paper_search_mcp_enabled: bool = False
    paper_search_mcp_path: str = ""
    paper_search_mcp_command: str = "uv"
    paper_search_mcp_module: str = "paper_search_mcp.server"

    anthropic_api_key: str = ""
    openai_api_key: str = ""

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


def _is_placeholder(value: str) -> bool:
    normalized = value.strip().lower()
    return "replace-with" in normalized or "change-me" in normalized


settings = Settings()
