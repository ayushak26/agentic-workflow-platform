from __future__ import annotations

import re
from pathlib import Path

import yaml

from scripts.generate_production_env import main as generate_environment
from scripts.production_preflight import parse_env

ROOT = Path(__file__).resolve().parents[1]


def test_only_https_proxy_is_public_in_production():
    compose = yaml.safe_load(
        (ROOT / "docker-compose.production.yml").read_text()
    )
    services = compose["services"]

    assert services["caddy"]["ports"] == [
        "80:80",
        "443:443",
        "443:443/udp",
    ]
    for name in ("app", "frontend", "mongo", "weaviate", "minio", "redis"):
        assert not services[name].get("ports"), name
    for name in ("prometheus", "grafana"):
        assert all(
            str(binding).startswith("127.0.0.1:")
            for binding in services[name]["ports"]
        )
    assert compose["networks"]["backend"]["internal"] is True


def test_application_container_is_read_only_and_unprivileged():
    compose = yaml.safe_load(
        (ROOT / "docker-compose.production.yml").read_text()
    )
    app = compose["services"]["app"]
    dockerfile = (ROOT / "Dockerfile").read_text()

    assert app["read_only"] is True
    assert app["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in app["security_opt"]
    assert "USER app" in dockerfile
    assert "--uid 10001" in dockerfile
    assert "uv sync --frozen" in dockerfile


def test_local_compose_build_has_no_mandatory_secret_interpolation():
    raw = (ROOT / "docker-compose.yml").read_text()

    assert ":?" not in raw
    for name in (
        "MONGO_ROOT_USERNAME",
        "MONGO_ROOT_PASSWORD",
        "REDIS_PASSWORD",
        "GRAFANA_ADMIN_PASSWORD",
    ):
        assert f"${{{name}:-" in raw


def test_production_generator_covers_every_compose_variable(
    tmp_path,
    monkeypatch,
):
    output = tmp_path / ".env.production"
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setattr(
        "sys.argv",
        [
            "generate_production_env.py",
            "--domain",
            "ai.example.com",
            "--email",
            "admin@example.com",
            "--output",
            str(output),
        ],
    )
    generate_environment()
    values = parse_env(output)
    raw = (ROOT / "docker-compose.production.yml").read_text()
    compose_variables = set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)", raw))

    assert compose_variables <= values.keys()
    assert output.stat().st_mode & 0o077 == 0
    assert values["ENVIRONMENT"] == "production"
    assert values["DEV_BYPASS_ENABLED"] == "false"
