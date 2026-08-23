"""Loader module.

Part of the workflow runtime: schema, loader, compiler, preflight, executor, hitl, and events.

Public symbols: load_workflow, load_workflow_from_string.
"""
from pathlib import Path
from .preflight import _load_unique_yaml
from .schema import WorkflowSpec


def load_workflow(path: str | Path) -> WorkflowSpec:
    """Load the workflow.

    Args:
        path (str | Path): Filesystem path.

    Returns:
        WorkflowSpec: The workflow.
    """
    with open(path) as f:
        raw = _load_unique_yaml(f.read())
    return WorkflowSpec(**raw)


def load_workflow_from_string(yaml_text: str) -> WorkflowSpec:
    """Load the workflow from string.

    Args:
        yaml_text (str): Workflow YAML text.

    Returns:
        WorkflowSpec: The workflow from string.
    """
    raw = _load_unique_yaml(yaml_text)
    return WorkflowSpec(**raw)
