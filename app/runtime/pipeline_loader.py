from __future__ import annotations

from pathlib import Path

from .pipeline_schema import PipelineSpec
from .preflight import _load_unique_yaml

WORKFLOWS_DIR = Path("workflows")
PIPELINES_DIR = WORKFLOWS_DIR / "pipelines"


def stage_workflow_path(workflow_name: str) -> Path:
    return WORKFLOWS_DIR / f"{workflow_name}.yaml"


def load_pipeline(path: str | Path) -> PipelineSpec:
    with open(path) as f:
        raw = _load_unique_yaml(f.read())
    return PipelineSpec(**raw)


def load_pipeline_from_string(yaml_text: str) -> PipelineSpec:
    raw = _load_unique_yaml(yaml_text)
    return PipelineSpec(**raw)
