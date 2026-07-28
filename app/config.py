from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
    env_file=( ".env",".env.local"),
    extra="ignore",
)
    secret_key: str = "insecure-dev-secret-change-me"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    environment: str = "development"

    mongo_uri: str = "mongodb://eurskem:eurschempass@localhost:27017"
    mongo_db: str = "eurskem_ai"

    retrieval_reranker_model: str = "claude-sonnet-4-5"
    retrieval_compressor_model: str = "claude-sonnet-4-5"

    weaviate_host: str = "weaviate"
    weaviate_port: int = 8080

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


settings = Settings()
