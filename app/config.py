"""Typed application settings, loaded from .env and OS environment."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Core app
    app_env: str = "local"
    log_level: str = "INFO"

    # Infrastructure endpoints
    mongo_url: str = "mongodb://mongo:27017"
    redis_url: str = "redis://redis:6379/0"
    weaviate_url: str = "http://weaviate:8080"
    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"

    # LLM provider keys — populated in Phase 5; empty here is fine
    anthropic_api_key: str = ""
    openai_api_key: str = ""


settings = Settings()