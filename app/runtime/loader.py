from pathlib import Path
from .preflight import _load_unique_yaml
from .schema import WorkflowSpec


def load_workflow(path: str | Path) -> WorkflowSpec:
    with open(path) as f:
        raw = _load_unique_yaml(f.read())
    return WorkflowSpec(**raw)


def load_workflow_from_string(yaml_text: str) -> WorkflowSpec:
    raw = _load_unique_yaml(yaml_text)
    return WorkflowSpec(**raw)
