"""Typed application settings, loaded from .env and OS environment.

Convention: Python attributes are lowercase (PEP 8). Env vars in .env are
UPPER_SNAKE. Pydantic-settings matches case-insensitively, so
OPENAI_API_KEY in .env populates settings.openai_api_key in code.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Core app
    app_env: str = "local"
    log_level: str = "INFO"

    # LLM provider keys
    openai_api_key: str
    anthropic_api_key: str = ""   # uncomment when Claude goes live

    # Retrieval — defaults flipped to GPT while OpenAI is the live provider.
    # YAML workflows targeting claude-* will route to the Anthropic stub.
    retrieval_reranker_model: str = "gpt-5"
    retrieval_compressor_model: str = "gpt-5-mini"
    weaviate_collection: str = "DocumentChunks"

    # Infrastructure endpoints
    mongo_url: str = "mongodb://mongo:27017"
    redis_url: str = "redis://redis:6379/0"
    weaviate_url: str = "http://weaviate:8080"
    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"


settings = Settings()