from pathlib import Path
import yaml
from .schema import WorkflowSpec


def load_workflow(path: str | Path) -> WorkflowSpec:
    with open(path) as f:
        raw = yaml.safe_load(f)
    return WorkflowSpec(**raw)


def load_workflow_from_string(yaml_text: str) -> WorkflowSpec:
    raw = yaml.safe_load(yaml_text)
    return WorkflowSpec(**raw)