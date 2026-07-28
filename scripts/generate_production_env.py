"""Generate every non-provider secret required by the IONOS Compose stack."""
from __future__ import annotations

import argparse
import os
import re
import secrets
from pathlib import Path

_DOMAIN = re.compile(
    r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,63}$"
)


def secret() -> str:
    return secrets.token_hex(32)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a secure .env.production for IONOS"
    )
    parser.add_argument("--domain", required=True, help="Example: ai.example.com")
    parser.add_argument("--email", required=True, help="ACME renewal email")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".env.production"),
    )
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    domain = args.domain.strip().lower()
    if not _DOMAIN.fullmatch(domain):
        raise SystemExit("Use a hostname such as ai.example.com, without https://")
    if args.output.exists() and not args.force:
        raise SystemExit(
            f"{args.output} already exists; use --force only when rotating all secrets"
        )

    mongo_password = secret()
    redis_password = secret()
    values = {
        "DOMAIN": domain,
        "ACME_EMAIL": args.email.strip(),
        "MAX_REQUEST_BODY_SIZE": "220MB",
        "WORKFLOWS_HOST_PATH": "/opt/eurskem/shared/workflows",
        "UVICORN_WORKERS": "2",
        "ENVIRONMENT": "production",
        "SECRET_KEY": secrets.token_urlsafe(64),
        "ALGORITHM": "HS256",
        "ACCESS_TOKEN_EXPIRE_MINUTES": "60",
        "JWT_ISSUER": "eurskem-ai",
        "JWT_AUDIENCE": "eurskem-ai-ui",
        "AUTH_MODE": "local",
        "DEV_BYPASS_ENABLED": "false",
        "API_DOCS_ENABLED": "false",
        "METRICS_ENABLED": "true",
        "CORS_ALLOWED_ORIGINS": f"https://{domain}",
        "TRUSTED_HOSTS": domain,
        "MAX_REQUEST_BODY_MB": "220",
        "WORKFLOW_FILE_MAX_MB": "50",
        "WORKFLOW_FILE_MAX_TOTAL_MB": "200",
        "WORKFLOW_FILE_MAX_FILES": "20",
        "GUARDRAILS_ENABLED": "true",
        "GUARDRAIL_PII_MODE": "audit",
        "GUARDRAIL_MAX_TEXT_CHARS": "2000000",
        "RATE_LIMIT_ENABLED": "true",
        "RATE_LIMIT_REQUESTS_PER_MINUTE": "60",
        "RATE_LIMIT_AUTH_REQUESTS_PER_MINUTE": "10",
        "MONGO_ROOT_USERNAME": "eurskem-root",
        "MONGO_ROOT_PASSWORD": secret(),
        "MONGO_APP_USERNAME": "eurskem-app",
        "MONGO_APP_PASSWORD": mongo_password,
        "MONGO_URI": (
            f"mongodb://eurskem-app:{mongo_password}@mongo:27017/"
            "eurskem_ai?authSource=eurskem_ai"
        ),
        "MONGO_DB": "eurskem_ai",
        "WEAVIATE_HOST": "weaviate",
        "WEAVIATE_PORT": "8080",
        "WEAVIATE_GRPC_PORT": "50051",
        "WEAVIATE_API_KEY_USER": "eurskem-app",
        "WEAVIATE_API_KEY": secret(),
        "MINIO_ROOT_USER": "eurskem-root",
        "MINIO_ROOT_PASSWORD": secret(),
        "MINIO_ACCESS_KEY": "eurskem-app",
        "MINIO_SECRET_KEY": secret(),
        "MINIO_ENDPOINT": "minio:9000",
        "MINIO_BUCKET": "eurskem-ai-docs",
        "REDIS_PASSWORD": redis_password,
        "REDIS_URL": f"redis://:{redis_password}@redis:6379/0",
        "READINESS_REQUIRED_SERVICES": (
            "mongo,weaviate,minio,redis,checkpointer,semantic_cache,mcp:eurskem"
        ),
        "HEALTH_PROBE_TIMEOUT_SECONDS": "2",
        "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY", ""),
        "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY", ""),
        "EXTERNAL_REQUEST_TIMEOUT_SECONDS": "30",
        "LLM_REQUEST_TIMEOUT_SECONDS": "120",
        "MCP_REQUEST_TIMEOUT_SECONDS": "30",
        "LLM_RETRY_ATTEMPTS": "3",
        "LLM_RETRY_BASE_DELAY_SECONDS": "1",
        "LLM_RETRY_MAX_DELAY_SECONDS": "8",
        "LLM_RETRY_JITTER_RATIO": "0.2",
        "LLM_MAX_INPUT_TOKENS": "32000",
        "LLM_MAX_OUTPUT_TOKENS": "8192",
        "LLM_USER_DAILY_BUDGET_USD": "5",
        "LLM_GLOBAL_DAILY_BUDGET_USD": "100",
        "LLM_EMERGENCY_MODEL": "gpt-5-mini",
        "LLM_EMERGENCY_MAX_INPUT_TOKENS": "2000",
        "SEMANTIC_CACHE_ENABLED": "true",
        "SEMANTIC_CACHE_SIMILARITY_THRESHOLD": "0.97",
        "SEMANTIC_CACHE_TTL_SECONDS": "3600",
        "SEMANTIC_CACHE_MAX_ENTRIES_PER_SCOPE": "200",
        "PAPER_SEARCH_MCP_ENABLED": "false",
        "PAPER_SEARCH_MCP_PATH": "",
        "PAPER_SEARCH_MCP_COMMAND": "uv",
        "PAPER_SEARCH_MCP_MODULE": "paper_search_mcp.server",
        "OTEL_ENABLED": "false",
        "OTEL_SERVICE_NAME": "eurskem-ai",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "",
        "GRAFANA_ADMIN_PASSWORD": secret(),
    }
    args.output.write_text(
        "\n".join(f"{key}={value}" for key, value in values.items()) + "\n",
        encoding="utf-8",
    )
    args.output.chmod(0o600)
    print(f"Created {args.output} with mode 0600.")
    if not values["OPENAI_API_KEY"]:
        print("Add OPENAI_API_KEY before deployment (required for semantic cache/RAG).")
    if not values["ANTHROPIC_API_KEY"]:
        print("ANTHROPIC_API_KEY is optional when OpenAI fallback is configured.")


if __name__ == "__main__":
    main()
