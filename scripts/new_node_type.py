#!/usr/bin/env python3
"""Scaffold one fully standardized workflow node module.

Discovery is automatic, so adding the generated file is sufficient to make
the type appear in the registry, Builder, AI catalog, compatibility engine,
and conformance suite. The script refuses to overwrite existing files.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re


TEMPLATE = '''"""{description}"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from app.nodes.base import NodeType
from app.nodes.contracts import DataType
from app.nodes.registry import NodeRegistry


class {class_name}Config(BaseModel):
    model_config = ConfigDict(extra="forbid")


class {class_name}Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class {class_name}Output(BaseModel):
    model_config = ConfigDict(extra="forbid")
    result: str


@NodeRegistry.register
class {class_name}(NodeType):
    type_name = "{class_name}"
    description = {description!r}
    input_schema = {class_name}Input
    output_schema = {class_name}Output
    config_schema = {class_name}Config
    accepts = {{DataType.STATE, DataType.TEXT, DataType.JSON}}
    produces = {{DataType.STATE, DataType.TEXT}}

    async def run(self, state, resolved_config: dict[str, Any]) -> dict[str, Any]:
        """Execute the capability and return a validated output mapping."""
        raise NotImplementedError("Implement {class_name}.run")
'''


def snake_case(value: str) -> str:
    """Convert a PascalCase type name to its module filename."""

    return re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("type_name", help="PascalCase registry/class name")
    parser.add_argument("--description", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Z][A-Za-z0-9]*", args.type_name):
        parser.error("type_name must be PascalCase alphanumeric")
    path = Path("app/nodes") / f"{snake_case(args.type_name)}.py"
    if path.exists():
        parser.error(f"refusing to overwrite {path}")
    path.write_text(TEMPLATE.format(
        class_name=args.type_name, description=args.description,
    ), encoding="utf-8")
    print(path)
    print("Next: implement run(), add a reference example, then run node conformance tests.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())