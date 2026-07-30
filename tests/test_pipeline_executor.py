from __future__ import annotations

import app.nodes  # noqa: F401  (registers Literal/Echo node types)
import pytest

from app.runtime.pipeline_executor import (
    PipelineExecutionError,
    advance_pipeline,
    materialize_stage_inputs,
    resolve_input_source,
    run_pipeline,
)
from app.runtime.pipeline_loader import load_pipeline_from_string
from app.runtime.pipeline_schema import PipelineSpec, PipelineStageSpec
from app.runtime.schema import WorkflowInputSpec

from .fake_mongo import InMemoryDB

STAGE_ONE_YAML = """
name: pipeline_stage_one
version: "1.0"
nodes:
  - id: greeting
    type: Literal
    config:
      value: {text: "hello from stage one"}
entry: greeting
exit: greeting
"""

STAGE_TWO_YAML = """
name: pipeline_stage_two
version: "1.0"
inputs:
  greeting:
    type: json
    required: true
nodes:
  - id: repeat_greeting
    type: Literal
    config:
      value: "{{ inputs.greeting.value.text }}"
entry: repeat_greeting
exit: repeat_greeting
"""

PIPELINE_YAML = """
name: two_stage_pipeline
version: "1.0"
stages:
  - id: evidence
    workflow: stage_one
  - id: drafts
    workflow: stage_two
"""


@pytest.fixture
def workflows_dir(tmp_path, monkeypatch):
    (tmp_path / "stage_one.yaml").write_text(STAGE_ONE_YAML)
    (tmp_path / "stage_two.yaml").write_text(STAGE_TWO_YAML)
    monkeypatch.setattr("app.runtime.pipeline_loader.WORKFLOWS_DIR", tmp_path)
    return tmp_path


def test_pipeline_schema_rejects_duplicate_stage_ids():
    with pytest.raises(ValueError, match="unique"):
        PipelineSpec(
            name="bad",
            stages=[
                {"id": "a", "workflow": "x"},
                {"id": "a", "workflow": "y"},
            ],
        )


def test_pipeline_schema_rejects_reserved_stage_id():
    with pytest.raises(ValueError, match="reserved"):
        PipelineStageSpec(id="inputs", workflow="x")


def test_pipeline_loader_parses_yaml():
    spec = load_pipeline_from_string(PIPELINE_YAML)
    assert spec.name == "two_stage_pipeline"
    assert [s.id for s in spec.stages] == ["evidence", "drafts"]


def test_resolve_input_source_precedence():
    stage = PipelineStageSpec(
        id="drafts", workflow="stage_two", inputs={"greeting": "{{ inputs.override }}"},
    )
    # Explicit mapping wins even though "greeting" also exists as a pipeline
    # input and as a node id in an earlier stage.
    kind, _ = resolve_input_source(
        "greeting", stage, {"greeting"}, [("evidence", {"greeting"})],
    )
    assert kind == "explicit_mapping"

    unmapped_stage = PipelineStageSpec(id="drafts", workflow="stage_two")
    kind, _ = resolve_input_source(
        "greeting", unmapped_stage, {"greeting"}, [("evidence", {"greeting"})],
    )
    assert kind == "pipeline_input"

    kind, source_stage = resolve_input_source(
        "greeting", unmapped_stage, set(), [("evidence", {"greeting"})],
    )
    assert kind == "auto_matched"
    assert source_stage == "evidence"

    kind, _ = resolve_input_source("missing", unmapped_stage, set(), [])
    assert kind == "unresolved"


def test_materialize_stage_inputs_auto_matches_and_unwraps_parsed():
    stage = PipelineStageSpec(id="drafts", workflow="stage_two")
    stage_spec_inputs = {
        "blueprint": WorkflowInputSpec(type="json", required=True),
        "as_text": WorkflowInputSpec(type="text", required=False),
    }

    class _FakeSpec:
        inputs = stage_spec_inputs

    stage_outputs_by_id = {
        "evidence": {
            "blueprint": {"raw": "{...}", "parsed": {"objective": "x"}},
            "as_text": {"objective": "x"},
        }
    }
    resolved = materialize_stage_inputs(stage, _FakeSpec(), {}, stage_outputs_by_id)
    # TransformAgent-style {raw, parsed} envelope is unwrapped for json inputs.
    assert resolved["blueprint"] == {"objective": "x"}
    # A non-string value for a text input is JSON-encoded, not str()-repr'd.
    assert resolved["as_text"] == '{\n  "objective": "x"\n}'


def test_materialize_stage_inputs_backfills_none_for_unresolved_optional_input():
    """An optional stage input with no explicit mapping, no matching
    pipeline input, and no matching upstream node still needs a key in the
    resolved dict — see the comment in materialize_stage_inputs for why
    (the same class of bug validate_workflow_inputs has for plain workflow
    runs). It must stay None, not become the JSON-encoded string "null"."""
    stage = PipelineStageSpec(id="drafts", workflow="stage_two")

    class _FakeSpec:
        inputs = {
            "optional_note": WorkflowInputSpec(type="text", required=False),
            "optional_blob": WorkflowInputSpec(type="json", required=False),
        }

    resolved = materialize_stage_inputs(stage, _FakeSpec(), {}, {})
    assert resolved == {"optional_note": None, "optional_blob": None}


@pytest.mark.asyncio
async def test_run_pipeline_then_advance_flows_output_into_next_stage(workflows_dir):
    db = InMemoryDB()
    spec = load_pipeline_from_string(PIPELINE_YAML)

    launch = await run_pipeline(
        pipeline_spec=spec,
        pipeline_yaml=PIPELINE_YAML,
        pipeline_run_id="pl-1",
        pipeline_inputs={},
        session="user@example.com",
        services={"audit_db": db},
    )

    assert launch["stage_id"] == "evidence"
    assert launch["stage_result"]["status"] == "completed"
    assert launch["pipeline"]["status"] == "gated"
    assert launch["pipeline"]["current_stage_index"] == 0

    advanced = await advance_pipeline(
        pipeline_run_id="pl-1",
        session="user@example.com",
        services={"audit_db": db},
    )

    assert advanced["stage_id"] == "drafts"
    assert advanced["stage_result"]["status"] == "completed"
    # stage_two's Literal node echoed {{ inputs.greeting.text }}, proving
    # stage_one's node output ("greeting" -> {"text": "hello from stage one"})
    # was auto-matched by name into stage_two's declared "greeting" input.
    drafts_run_id = advanced["stage_run_id"]
    drafts_run = await db["run_history"].find_one({"run_id": drafts_run_id})
    assert drafts_run["outputs"]["repeat_greeting"]["value"] == "hello from stage one"

    assert advanced["pipeline"]["status"] == "completed"
    assert advanced["pipeline"]["stages"][0]["status"] == "completed"
    assert advanced["pipeline"]["stages"][1]["status"] == "completed"


@pytest.mark.asyncio
async def test_advance_before_gated_is_rejected(workflows_dir):
    db = InMemoryDB()
    spec = load_pipeline_from_string(PIPELINE_YAML)
    await run_pipeline(
        pipeline_spec=spec,
        pipeline_yaml=PIPELINE_YAML,
        pipeline_run_id="pl-2",
        pipeline_inputs={},
        session="user@example.com",
        services={"audit_db": db},
    )
    # Pipeline is now "gated" after stage 0 — advance once, then a second
    # advance with no further stages must raise, not silently no-op.
    await advance_pipeline(
        pipeline_run_id="pl-2", session="user@example.com", services={"audit_db": db},
    )
    with pytest.raises(PipelineExecutionError, match="not gated|no further stages"):
        await advance_pipeline(
            pipeline_run_id="pl-2", session="user@example.com", services={"audit_db": db},
        )
