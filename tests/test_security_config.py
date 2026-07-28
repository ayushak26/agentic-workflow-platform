from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings


def production_settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "environment": "production",
        "secret_key": "a-unique-production-secret-key-32-bytes",
        "dev_bypass_enabled": False,
        "api_docs_enabled": False,
        "semantic_cache_enabled": True,
        "openai_api_key": "test-openai-key",
        "mongo_uri": (
            "mongodb://app-user:unique-password@mongo:27017/"
            "?authSource=admin"
        ),
        "weaviate_api_key": "unique-weaviate-key",
        "minio_access_key": "app-user",
        "minio_secret_key": "unique-minio-secret",
        "redis_url": "redis://:unique-redis-password@redis:6379/0",
        "cors_allowed_origins": "https://ai.eurskem.example",
        "trusted_hosts": "ai.eurskem.example",
    }
    values.update(overrides)
    return Settings(**values)


def test_secure_production_settings_are_accepted():
    settings = production_settings()

    assert settings.allowed_cors_origins == (
        "https://ai.eurskem.example",
    )
    assert settings.allowed_hosts == ("ai.eurskem.example",)


@pytest.mark.parametrize(
    ("field", "unsafe_value"),
    [
        ("secret_key", "short"),
        ("secret_key", "replace-with-a-unique-random-secret"),
        ("dev_bypass_enabled", True),
        ("api_docs_enabled", True),
        ("semantic_cache_enabled", False),
        ("rate_limit_enabled", False),
        ("guardrails_enabled", False),
        ("mongo_uri", "mongodb://eurskem:eurschempass@mongo:27017"),
        ("weaviate_api_key", ""),
        ("weaviate_api_key", "replace-with-local-weaviate-api-key"),
        ("minio_secret_key", "eurskempassword"),
        ("minio_secret_key", "replace-with-local-minio-password"),
        ("redis_url", "redis://redis:6379/0"),
        (
            "redis_url",
            "redis://:replace-with-local-redis-password@redis:6379/0",
        ),
        ("cors_allowed_origins", "*"),
        ("cors_allowed_origins", "http://localhost:5173"),
        ("trusted_hosts", "*"),
        ("trusted_hosts", "testserver"),
    ],
)
def test_unsafe_production_settings_are_rejected(field, unsafe_value):
    with pytest.raises(ValidationError, match="Unsafe production configuration"):
        production_settings(**{field: unsafe_value})


def test_development_defaults_remain_available_for_local_tests():
    settings = Settings(_env_file=None)

    assert settings.environment == "development"
    assert settings.dev_bypass_enabled is True
    assert "testserver" in settings.allowed_hosts
