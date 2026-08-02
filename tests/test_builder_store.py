from __future__ import annotations

import pytest

from app.workflow.builder_store import WorkflowBuilderStore

SAMPLE_YAML_V1 = "name: Sample\nversion: '1.0'\nnodes: []\n"
SAMPLE_YAML_V2 = "name: Sample\nversion: '1.0'\ndescription: v2\nnodes: []\n"


@pytest.fixture
def store(tmp_path) -> WorkflowBuilderStore:
    return WorkflowBuilderStore(tmp_path)


def test_validate_name_and_version_id_reject_unsafe_values(store):
    with pytest.raises(ValueError):
        WorkflowBuilderStore.validate_name("../escape")
    with pytest.raises(ValueError):
        WorkflowBuilderStore.validate_name("has space")
    with pytest.raises(ValueError):
        WorkflowBuilderStore.validate_version_id("../../etc/passwd")
    assert WorkflowBuilderStore.validate_name("abc_DEF-123") == "abc_DEF-123"


def test_draft_save_read_delete_round_trip(store):
    assert store.read_draft("wf") is None

    document = store.save_draft("wf", SAMPLE_YAML_V1, canvas={"viewport": {"x": 0}})
    assert document["yaml"] == SAMPLE_YAML_V1
    assert document["canvas"] == {"viewport": {"x": 0}}
    assert document["base_sha256"] is None  # no committed workflow file yet

    read_back = store.read_draft("wf")
    assert read_back is not None
    assert read_back["sha256"] == document["sha256"]
    # No committed file yet, so the draft "differs" from nothing.
    assert read_back["current_sha256"] is None
    assert read_back["differs_from_current"] is True

    assert store.delete_draft("wf") is True
    assert store.read_draft("wf") is None
    assert store.delete_draft("wf") is False


def test_draft_differs_from_current_after_save(store):
    store.save_workflow("wf", SAMPLE_YAML_V1)
    store.save_draft("wf", SAMPLE_YAML_V1)
    same = store.read_draft("wf")
    assert same["differs_from_current"] is False

    store.save_draft("wf", SAMPLE_YAML_V2)
    changed = store.read_draft("wf")
    assert changed["differs_from_current"] is True


def test_record_version_dedups_identical_content(store):
    first = store.record_version("wf", SAMPLE_YAML_V1)
    second = store.record_version("wf", SAMPLE_YAML_V1)
    assert first == second
    assert len(store.list_versions("wf")) == 1

    third = store.record_version("wf", SAMPLE_YAML_V2)
    assert third != first
    assert len(store.list_versions("wf")) == 2


def test_save_workflow_writes_file_versions_and_clears_draft(store):
    store.save_draft("wf", SAMPLE_YAML_V1)
    version_id = store.save_workflow("wf", SAMPLE_YAML_V1)

    assert store.workflow_path("wf").read_text(encoding="utf-8") == SAMPLE_YAML_V1
    assert store.read_draft("wf") is None
    versions = store.list_versions("wf")
    assert len(versions) == 1
    assert versions[0]["version_id"] == version_id
    assert versions[0]["current"] is True

    # A second distinct save creates a second version and keeps both.
    second_version_id = store.save_workflow("wf", SAMPLE_YAML_V2)
    assert second_version_id != version_id
    versions = store.list_versions("wf")
    assert len(versions) == 2
    current = [v for v in versions if v["current"]]
    assert len(current) == 1
    assert current[0]["version_id"] == second_version_id


def test_get_version_raises_for_unknown_version(store):
    store.save_workflow("wf", SAMPLE_YAML_V1)
    with pytest.raises(FileNotFoundError):
        store.get_version("wf", "does-not-exist")


def test_restore_version_writes_back_and_preserves_history(store):
    v1 = store.save_workflow("wf", SAMPLE_YAML_V1)
    store.save_workflow("wf", SAMPLE_YAML_V2)

    restored_yaml, restored_version_id = store.restore_version("wf", v1)
    assert restored_yaml == SAMPLE_YAML_V1
    assert store.workflow_path("wf").read_text(encoding="utf-8") == SAMPLE_YAML_V1

    # Restoring v1 over v2 is itself a new "current" save; both prior
    # versions remain listed (immutable history), and restoring dedups back
    # onto the original v1 version id rather than minting a redundant one.
    versions = store.list_versions("wf")
    assert restored_version_id == v1
    assert len(versions) == 2
    current = [v for v in versions if v["current"]]
    assert len(current) == 1
    assert current[0]["version_id"] == v1


def test_delete_workflow_removes_yaml_draft_and_versions(store):
    store.save_workflow("wf", SAMPLE_YAML_V1)
    store.save_workflow("wf", SAMPLE_YAML_V2)
    store.save_draft("wf", SAMPLE_YAML_V2, canvas={"viewport": {"x": 0}})
    assert store.workflow_path("wf").exists()
    assert store.versions_dir("wf").exists()
    assert store.read_draft("wf") is not None

    deleted = store.delete_workflow("wf")

    assert deleted is True
    assert not store.workflow_path("wf").exists()
    assert not store.versions_dir("wf").exists()
    assert store.read_draft("wf") is None


def test_delete_workflow_returns_false_when_nothing_to_delete(store):
    assert store.delete_workflow("never_existed") is False


def test_delete_workflow_is_scoped_to_the_named_workflow_only(store):
    store.save_workflow("wf_a", SAMPLE_YAML_V1)
    store.save_workflow("wf_b", SAMPLE_YAML_V1)

    store.delete_workflow("wf_a")

    assert not store.workflow_path("wf_a").exists()
    assert store.workflow_path("wf_b").exists()
    assert store.versions_dir("wf_b").exists()
