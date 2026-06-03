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

    redis_url: str = "redis://localhost:6379/0"

    anthropic_api_key: str = ""
    openai_api_key: str = ""

    dev_bypass_enabled: bool = True
    dev_bypass_username: str = "ayush"
    dev_bypass_password: str = "dev123"


settings = Settings()