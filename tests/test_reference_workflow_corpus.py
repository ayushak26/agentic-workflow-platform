"""Coverage and UI-isolation contract for the hidden 400-workflow corpus."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import app.nodes  # noqa: F401
from app.api import workflows as workflow_api
from app.nodes.registry import NodeRegistry
from app.workflow.builder_store import WorkflowBuilderStore
from scripts.generate_reference_workflows import slug


ROOT = Path(__file__).resolve().parents[1]
GENERATED = ROOT / "workflows" / "reference" / "generated"


def test_generated_corpus_has_exact_target_and_every_live_type():
    files = sorted(GENERATED.rglob("*.yaml"))
    assert len(files) == 400
    directories = {path.parent.name for path in files}
    expected = {slug(name) for name in NodeRegistry._registry}
    assert directories == expected

    for path in files:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        node_types = {
            node.get("type") for node in document.get("nodes", []) if isinstance(node, dict)
        }
        directory_type = next(
            name for name in NodeRegistry._registry
            if slug(name) == path.parent.name
        )
        assert directory_type in node_types


def test_reference_corpus_is_not_exposed_by_library_listing(monkeypatch):
    monkeypatch.setattr(workflow_api, "WORKFLOWS_DIR", ROOT / "workflows")
    names = {entry["name"] for entry in workflow_api.list_workflows()}
    assert all(not name.startswith("example_") for name in names)
    assert not names & {path.stem for path in GENERATED.rglob("*.yaml")}


def test_builder_store_cannot_address_reference_subdirectories(tmp_path):
    store = WorkflowBuilderStore(tmp_path)
    with pytest.raises(ValueError):
        store.workflow_path("reference/generated/example_01")