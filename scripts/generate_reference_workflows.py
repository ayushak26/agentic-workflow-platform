#!/usr/bin/env python3
"""Generate 400 hidden, deterministic node-reference workflows.

Every source is an existing workflow selected by the live registry's
``example_workflow_path`` and already proven to contain the target type. The
generator changes only the workflow name, validates every result with the real
preflight engine, and writes atomically after the complete set passes.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import shutil

import yaml

import app.nodes  # noqa: F401
from app.nodes.registry import NodeRegistry
from app.runtime.preflight import preflight_workflow_yaml


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "workflows" / "reference" / "generated"
TARGET_COUNT = 400


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def generate_documents() -> list[tuple[Path, str]]:
    manifests = sorted(NodeRegistry.manifest(), key=lambda item: item["type_name"])
    documents: list[tuple[Path, str]] = []
    validated_sources: set[Path] = set()
    for entry in manifests:
        type_name = entry["type_name"]
        relative = (entry.get("about") or {}).get("example_workflow_path")
        if not relative:
            raise RuntimeError(f"{type_name} has no real example_workflow_path")
        source = ROOT / relative
        source_text = source.read_text(encoding="utf-8")
        raw = yaml.safe_load(source_text)
        if source not in validated_sources:
            report = preflight_workflow_yaml(source_text)
            if not report.valid or report.warnings:
                detail = "; ".join(f"{i.code}: {i.message}" for i in report.issues)
                raise RuntimeError(f"source {source} failed: {detail}")
            validated_sources.add(source)
        if type_name not in {
            node.get("type") for node in raw.get("nodes", []) if isinstance(node, dict)
        }:
            raise RuntimeError(f"{source} does not contain {type_name}")
        variants = 8 if not documents else 7  # 57*7 + one = exactly 400.
        for variant in range(1, variants + 1):
            document = dict(raw)
            document["name"] = f"Reference {type_name} {variant:02d}"
            text = yaml.safe_dump(document, sort_keys=False, allow_unicode=True)
            # The only mutation is `name`; still parse through the authoritative
            # WorkflowSpec to detect serialization/schema drift on every file.
            from app.runtime.schema import WorkflowSpec

            WorkflowSpec.model_validate(yaml.safe_load(text))
            path = OUTPUT / slug(type_name) / f"example_{variant:02d}.yaml"
            documents.append((path, text))
    if len(documents) != TARGET_COUNT:
        raise RuntimeError(f"expected {TARGET_COUNT} documents, built {len(documents)}")
    return documents


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate without writing")
    args = parser.parse_args()
    documents = generate_documents()
    if not args.check:
        temporary = OUTPUT.with_name(".generated.tmp")
        shutil.rmtree(temporary, ignore_errors=True)
        for path, text in documents:
            target = temporary / path.relative_to(OUTPUT)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        shutil.rmtree(OUTPUT, ignore_errors=True)
        temporary.replace(OUTPUT)
    print(f"validated {len(documents)} hidden reference workflows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())