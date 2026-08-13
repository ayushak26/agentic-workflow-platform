from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.health import router
from app.config import Settings
from app.ingestion.extractor import supported_extensions
from app.mcp.client import build_server_specs
from app.workflow.file_inputs import (
    FILE_CATEGORY_EXTENSIONS,
    REFERENCE_ONLY_EXTENSIONS,
    TEXT_EXTRACTABLE_EXTENSIONS,
)


class FakeMongoDatabase:
    async def command(self, command: str):
        assert command == "ping"
        return {"ok": 1}


class FakeWeaviate:
    def is_ready(self):
        return True


class FakeS3:
    def head_bucket(self, *, Bucket: str):
        assert Bucket == "documents"
        return {}


class FakeObjectStore:
    bucket = "documents"
    client = FakeS3()


class FakeRedis:
    def __init__(self, *, available: bool = True):
        self.available = available

    async def ping(self):
        if not self.available:
            raise ConnectionError("redis unavailable")
        return True


class FakeMCP:
    def __init__(self, servers=("eurskem",), unavailable=()):
        self.configured_servers = servers
        self.unavailable = set(unavailable)

    async def probe(self, server: str):
        if server in self.unavailable:
            raise ConnectionError(f"{server} unavailable")
        return True


class FakeCheckpointer:
    async def aget(self, config):
        assert config["configurable"]["thread_id"] == "__readiness__"
        return None


def readiness_app(*, redis_available: bool = True) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.services = {
        "audit_db": FakeMongoDatabase(),
        "weaviate_client": FakeWeaviate(),
        "object_store": FakeObjectStore(),
        "redis": FakeRedis(available=redis_available),
        "langgraph_checkpointer": FakeCheckpointer(),
        "mcp_client": FakeMCP(),
    }
    return app


def test_health_and_ready_probe_live_dependencies():
    with TestClient(readiness_app()) as client:
        health = client.get("/health")
        ready = client.get("/ready")

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert ready.status_code == 200
    assert ready.json()["ready"] is True
    assert all(
        result["status"] == "ok"
        for result in ready.json()["services"].values()
    )


def test_health_stays_live_while_ready_fails_on_dependency_error():
    with TestClient(readiness_app(redis_available=False)) as client:
        health = client.get("/health")
        ready = client.get("/ready")

    assert health.status_code == 200
    assert health.json()["status"] == "degraded"
    assert health.json()["services"]["redis"]["status"] == "unavailable"
    assert ready.status_code == 503


def test_ready_requires_every_enabled_mcp_server():
    app = readiness_app()
    app.state.services["mcp_client"] = FakeMCP(
        servers=("eurskem", "paper-search-mcp"),
        unavailable=("paper-search-mcp",),
    )

    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    body = response.json()
    assert "mcp:paper-search-mcp" in body["required_services"]
    assert body["services"]["mcp:paper-search-mcp"]["status"] == "unavailable"


def test_paper_search_server_path_is_configuration_driven():
    disabled = build_server_specs(
        Settings(_env_file=None, paper_search_mcp_enabled=False)
    )
    # Asserts the paper-search server specifically, not the whole set: other
    # built-in MCP servers (Dynamics 365) are launched from the same table and
    # their presence says nothing about this setting.
    assert "paper-search-mcp" not in disabled
    assert "eurskem" in disabled

    enabled = build_server_specs(
        Settings(
            _env_file=None,
            paper_search_mcp_enabled=True,
            paper_search_mcp_path="/srv/paper-search-mcp",
        )
    )
    spec = enabled["paper-search-mcp"]
    # The launch target is always our adapter, so a source-checkout path
    # never appears in argv (which every process on the host can read via
    # `ps aux`) — it flows through PAPER_SEARCH_MCP_SOURCE_PATH instead,
    # which app/mcp/paper_search_server.py's _prepare_source_checkout()
    # inserts onto sys.path before importing paper_search_mcp.
    assert spec.args == ["-m", "app.mcp.paper_search_server"]
    assert spec.env["PAPER_SEARCH_MCP_SOURCE_PATH"] == "/srv/paper-search-mcp"
    assert not any("/Users/ayushkhandelwal" in item for item in spec.args)


def test_upload_contract_only_advertises_real_text_extractors():
    advertised_text = {
        extension
        for category, extensions in FILE_CATEGORY_EXTENSIONS.items()
        if category != "image"
        for extension in extensions
    }
    assert advertised_text == set(TEXT_EXTRACTABLE_EXTENSIONS)
    assert set(TEXT_EXTRACTABLE_EXTENSIONS) == set(supported_extensions())
    assert set(REFERENCE_ONLY_EXTENSIONS) == set(
        FILE_CATEGORY_EXTENSIONS["image"]
    )
    for unsupported in {".doc", ".odt", ".ppt", ".odp", ".csv", ".ods", ".xls"}:
        assert unsupported not in advertised_text
