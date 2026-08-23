"""Pipeline loader module.

Part of the workflow runtime: schema, loader, compiler, preflight, executor, hitl, and events.

Public symbols: stage_workflow_path, load_pipeline, load_pipeline_from_string.
"""
from __future__ import annotations

from pathlib import Path

from .pipeline_schema import PipelineSpec
from .preflight import _load_unique_yaml

WORKFLOWS_DIR = Path("workflows")
PIPELINES_DIR = WORKFLOWS_DIR / "pipelines"


def stage_workflow_path(workflow_name: str) -> Path:
    """Compute the stage workflow path.

    Args:
        workflow_name (str): Workflow name.

    Returns:
        Path: The workflow path.
    """
    return WORKFLOWS_DIR / f"{workflow_name}.yaml"


def load_pipeline(path: str | Path) -> PipelineSpec:
    """Load the pipeline.

    Args:
        path (str | Path): Filesystem path.

    Returns:
        PipelineSpec: The pipeline.
    """
    with open(path) as f:
        raw = _load_unique_yaml(f.read())
    return PipelineSpec(**raw)


def load_pipeline_from_string(yaml_text: str) -> PipelineSpec:
    """Load the pipeline from string.

    Args:
        yaml_text (str): Workflow YAML text.

    Returns:
        PipelineSpec: The pipeline from string.
    """
    raw = _load_unique_yaml(yaml_text)
    return PipelineSpec(**raw)
