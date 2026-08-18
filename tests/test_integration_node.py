"""The Integration capability.

Two design claims are under test. First, that one node with a provider +
operation selector genuinely replaces a node type per cloud-storage vendor —
the same node config shape works against two different fake adapters.
Second, that file bytes never enter workflow state directly: get_file must
write through object storage and return a pointer, and a rejected/expired
token must surface as a normal `reauth_required` output rather than an
uncaught exception.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.integrations.files.base import (
    CloudFileMeta,
    DownloadedFile,
    IntegrationAuthError,
    IntegrationConnection,
    IntegrationNotFoundError,
    IntegrationProvider,
    Page,
)
from app.integrations.files.service import IntegrationConnectionError, IntegrationService
from app.nodes.integration import IntegrationAgent


class FakeProvider(IntegrationProvider):
    """A minimal in-memory adapter — one small folder tree, no HTTP."""

    def __init__(self, provider_name: str, *, raise_auth_error: bool = False):
        self.provider = provider_name
        self._raise_auth_error = raise_auth_error
        self.downloaded_file_ids: list[str] = []

    def _files(self) -> list[CloudFileMeta]:
        return [
            CloudFileMeta(id="folder-1", name="Reports", is_folder=True, parent_id=None),
            CloudFileMeta(id="folder-2", name="Archive", is_folder=True, parent_id=None),
            CloudFileMeta(
                id="file-1",
                name="Q3.pdf",
                mime_type="application/pdf",
                size_bytes=1024,
                parent_id="folder-1",
                modified_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
            CloudFileMeta(
                id="file-2",
                name="Notes.txt",
                mime_type="text/plain",
                size_bytes=256,
                parent_id="folder-1",
                modified_at=datetime(2026, 1, 2, tzinfo=UTC),
            ),
            CloudFileMeta(
                id="file-3",
                name="Weird.xyz",
                mime_type="application/octet-stream",
                size_bytes=64,
                parent_id="folder-1",
            ),
        ]

    def _content_for(self, file_id: str) -> bytes:
        return {
            "file-1": b"%PDF-fake-bytes",
            "file-2": b"plain text notes",
            "file-3": b"unsupported-bytes",
        }[file_id]

    async def list_folder(self, connection, *, folder_id, page_size, page_token):
        if self._raise_auth_error:
            raise IntegrationAuthError("token revoked")
        items = [f for f in self._files() if f.parent_id == folder_id]
        return Page(items=items, next_page_token=None)

    async def search_files(self, connection, *, query, folder_id, page_size, page_token):
        items = [f for f in self._files() if query.lower() in f.name.lower()]
        return Page(items=items, next_page_token=None)

    async def get_file_meta(self, connection, *, file_id):
        for f in self._files():
            if f.id == file_id:
                return f
        raise IntegrationNotFoundError(f"no such file {file_id!r}")

    async def download_file(self, connection, *, file_id):
        self.downloaded_file_ids.append(file_id)
        meta = await self.get_file_meta(connection, file_id=file_id)
        content_type = "application/pdf" if file_id == "file-1" else "text/plain"
        return DownloadedFile(meta=meta, content=self._content_for(file_id), content_type=content_type)


class FakeObjectStore:
    def __init__(self):
        self.stored: dict[str, bytes] = {}

    def put_bytes(self, data: bytes, key: str, content_type: str | None = None) -> str:
        self.stored[key] = data
        return key


def make_service(*, raise_auth_error: bool = False) -> IntegrationService:
    provider = FakeProvider("google_drive", raise_auth_error=raise_auth_error)
    return IntegrationService(
        providers={"google_drive": provider},
        connections={
            "drive_conn": IntegrationConnection(
                id="drive_conn", provider="google_drive", address="me@example.com"
            )
        },
    )


def node(config: dict, service: IntegrationService, store: FakeObjectStore | None = None) -> IntegrationAgent:
    instance = IntegrationAgent("integration_step", config)
    instance.services = {"files_integration": service, "object_store": store or FakeObjectStore()}
    return instance


async def run(instance: IntegrationAgent) -> dict:
    return await instance.run({}, instance.config.model_dump())


class TestOperations:
    @pytest.mark.asyncio
    async def test_list_folder_returns_children(self):
        service = make_service()
        output = await run(
            node({"provider": "google_drive", "connection": "drive_conn", "operation": "list_folder", "folder_id": "folder-1"}, service)
        )
        assert output["status"] == "ok"
        assert output["count"] == 3
        assert {f["name"] for f in output["files"]} == {"Q3.pdf", "Notes.txt", "Weird.xyz"}
        assert output["first"]["id"] == "file-1"

    @pytest.mark.asyncio
    async def test_search_files_matches_by_name(self):
        service = make_service()
        output = await run(
            node({"provider": "google_drive", "connection": "drive_conn", "operation": "search_files", "query": "Q3"}, service)
        )
        assert output["found"] is True
        assert output["files"][0]["id"] == "file-1"

    @pytest.mark.asyncio
    async def test_search_files_empty_result_is_not_found(self):
        service = make_service()
        output = await run(
            node({"provider": "google_drive", "connection": "drive_conn", "operation": "search_files", "query": "nonexistent"}, service)
        )
        assert output["found"] is False
        assert output["first"] is None
        assert output["count"] == 0

    @pytest.mark.asyncio
    async def test_select_file_returns_metadata_without_downloading(self):
        service = make_service()
        provider = service.providers["google_drive"]
        output = await run(
            node({"provider": "google_drive", "connection": "drive_conn", "operation": "select_file", "file_id": "file-1"}, service)
        )
        assert output["file"]["id"] == "file-1"
        assert output["downloaded_file"] is None
        assert provider.downloaded_file_ids == []

    @pytest.mark.asyncio
    async def test_get_file_downloads_through_object_store_and_returns_a_workflow_file_ref(self):
        service = make_service()
        store = FakeObjectStore()
        output = await run(
            node({"provider": "google_drive", "connection": "drive_conn", "operation": "get_file", "file_id": "file-1"}, service, store)
        )
        ref = output["downloaded_file"]
        assert ref is not None
        # Same contract WorkflowFileLoader consumes — a workflow author wires
        # {{outputs.this_step.downloaded_file}} straight into its `files`
        # config, exactly like an uploaded file.
        assert ref["kind"] == "workflow_file"
        assert ref["name"] == "Q3.pdf"
        assert ref["extension"] == ".pdf"
        assert ref["category"] == "pdf"
        assert ref["parseable_text"] is True
        assert len(ref["sha256"]) == 64
        assert ref["minio_key"] in store.stored
        assert store.stored[ref["minio_key"]] == b"%PDF-fake-bytes"
        assert output["downloaded_files"] == [ref]
        # The raw bytes never appear anywhere else in the output.
        assert "content" not in output["file"]

    @pytest.mark.asyncio
    async def test_get_file_with_an_unsupported_extension_is_a_clean_error(self):
        service = make_service()
        output = await run(
            node({"provider": "google_drive", "connection": "drive_conn", "operation": "get_file", "file_id": "file-3"}, service)
        )
        assert output["status"] == "error"
        assert output["error_code"] == "unsupported_file_type"
        assert output["downloaded_file"] is None

    @pytest.mark.asyncio
    async def test_get_file_with_multiple_ids_downloads_all_and_returns_a_list(self):
        service = make_service()
        store = FakeObjectStore()
        output = await run(
            node(
                {
                    "provider": "google_drive", "connection": "drive_conn", "operation": "get_file",
                    "file_id": ["file-1", "file-2"],
                },
                service, store,
            )
        )
        assert output["status"] == "ok"
        assert len(output["downloaded_files"]) == 2
        names = {ref["name"] for ref in output["downloaded_files"]}
        assert names == {"Q3.pdf", "Notes.txt"}
        # Convenience singular field is the first of the collection.
        assert output["downloaded_file"] == output["downloaded_files"][0]
        assert output["count"] == 2
        assert store.stored[output["downloaded_files"][0]["minio_key"]] == b"%PDF-fake-bytes"
        assert store.stored[output["downloaded_files"][1]["minio_key"]] == b"plain text notes"

    @pytest.mark.asyncio
    async def test_select_file_with_multiple_ids_returns_metadata_for_each_without_downloading(self):
        service = make_service()
        provider = service.providers["google_drive"]
        output = await run(
            node(
                {
                    "provider": "google_drive", "connection": "drive_conn", "operation": "select_file",
                    "file_id": ["file-1", "file-2"],
                },
                service,
            )
        )
        assert [f["id"] for f in output["files"]] == ["file-1", "file-2"]
        assert output["downloaded_files"] == []
        assert provider.downloaded_file_ids == []

    @pytest.mark.asyncio
    async def test_select_folder_with_multiple_ids_returns_metadata_for_each(self):
        service = make_service()
        output = await run(
            node(
                {
                    "provider": "google_drive", "connection": "drive_conn", "operation": "select_folder",
                    "folder_id": ["folder-1", "folder-2"],
                },
                service,
            )
        )
        assert {f["id"] for f in output["files"]} == {"folder-1", "folder-2"}
        assert output["count"] == 2

    @pytest.mark.asyncio
    async def test_get_file_missing_file_id_is_a_config_error(self):
        with pytest.raises(ValueError):
            IntegrationAgent(
                "integration_step",
                {"provider": "google_drive", "connection": "drive_conn", "operation": "get_file"},
            )

    @pytest.mark.asyncio
    async def test_search_files_missing_query_is_a_config_error(self):
        with pytest.raises(ValueError):
            IntegrationAgent(
                "integration_step",
                {"provider": "google_drive", "connection": "drive_conn", "operation": "search_files"},
            )

    @pytest.mark.asyncio
    async def test_select_folder_missing_folder_id_is_a_config_error(self):
        with pytest.raises(ValueError):
            IntegrationAgent(
                "integration_step",
                {"provider": "google_drive", "connection": "drive_conn", "operation": "select_folder"},
            )

    @pytest.mark.asyncio
    async def test_an_empty_file_id_list_is_a_config_error_same_as_none(self):
        with pytest.raises(ValueError):
            IntegrationAgent(
                "integration_step",
                {"provider": "google_drive", "connection": "drive_conn", "operation": "get_file", "file_id": []},
            )


class TestFailureModes:
    @pytest.mark.asyncio
    async def test_a_revoked_token_surfaces_as_reauth_required_not_an_exception(self):
        service = make_service(raise_auth_error=True)
        output = await run(
            node({"provider": "google_drive", "connection": "drive_conn", "operation": "list_folder"}, service)
        )
        assert output["status"] == "reauth_required"
        assert output["error_code"] == "reauth_required"
        assert output["files"] == []

    @pytest.mark.asyncio
    async def test_a_missing_file_is_a_plain_not_found_error(self):
        service = make_service()
        output = await run(
            node({"provider": "google_drive", "connection": "drive_conn", "operation": "select_file", "file_id": "nope"}, service)
        )
        assert output["status"] == "error"
        assert output["error_code"] == "not_found"

    @pytest.mark.asyncio
    async def test_an_unknown_connection_is_not_connected_not_an_exception(self):
        service = make_service()
        output = await run(
            node({"provider": "google_drive", "connection": "does_not_exist", "operation": "list_folder"}, service)
        )
        assert output["status"] == "not_connected"
        assert output["error_code"] == "not_connected"

    @pytest.mark.asyncio
    async def test_no_service_configured_is_not_connected(self):
        instance = IntegrationAgent(
            "integration_step",
            {"provider": "google_drive", "connection": "drive_conn", "operation": "list_folder"},
        )
        instance.services = {}
        output = await instance.run({}, instance.config.model_dump())
        assert output["status"] == "not_connected"


class TestConnectionResolution:
    def test_an_unknown_connection_names_what_is_available(self):
        service = make_service()
        with pytest.raises(IntegrationConnectionError, match="drive_conn"):
            service.connection("nope")

    def test_connection_descriptions_never_include_credentials(self):
        service = IntegrationService(
            providers={"google_drive": FakeProvider("google_drive")},
            connections={
                "drive_conn": IntegrationConnection(
                    id="drive_conn",
                    provider="google_drive",
                    address="me@example.com",
                    credentials={"access_token": "secret-token"},
                )
            },
        )
        descriptions = service.describe_connections()
        assert descriptions == [
            {
                "id": "drive_conn",
                "provider": "google_drive",
                "display_name": "drive_conn",
                "address": "me@example.com",
                "needs_reauth": False,
            }
        ]
