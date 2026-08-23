#!/usr/bin/env python3
"""Build a complete, evidence-based QA matrix for every workflow YAML file.

This inventory never executes workflow nodes or calls providers. It records
static/preflight evidence and marks unsupported or unexecuted coverage
explicitly instead of turning absence of evidence into a passing result.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from app.runtime.preflight import preflight_workflow_yaml


MODES = (
    ("studio", "Workflow Studio"),
    ("knowledge", "Knowledge Studio"),
    ("eval", "Evaluation Lab"),
    ("cost", "Cost Management"),
)

INTEGRATION_NODE_MARKERS = (
    "MCP",
    "Email",
    "External",
    "Database",
    "StructuredDataset",
    "DeepResearch",
    "WebSearch",
    "Knowledge",
    "Retriever",
    "File",
)


def workflow_paths(root: Path) -> list[Path]:
    return sorted(
        path
        for path in {*root.rglob("*.yaml"), *root.rglob("*.yml")}
        if "collections" not in path.relative_to(root).parts
    )


def category(path: Path) -> str:
    value = path.as_posix()
    if "/.builder/versions/" in value:
        return "builder_version"
    if "/.builder/" in value:
        return "builder_state"
    if "/test_fixtures/" in value:
        return "test_fixture"
    if "/reference/generated/" in value:
        return "reference_generated"
    if "/reference/" in value:
        return "reference"
    if path.parent == Path("workflows"):
        return "catalog"
    return "support_nested"


def safe_document(path: Path) -> dict[str, Any]:
    import yaml

    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def input_summary(document: dict[str, Any]) -> dict[str, Any]:
    raw = document.get("inputs")
    if not isinstance(raw, dict):
        return {"names": [], "required": [], "types": {}}
    names: list[str] = []
    required: list[str] = []
    types: dict[str, str] = {}
    for name, spec in raw.items():
        names.append(str(name))
        if isinstance(spec, dict):
            types[str(name)] = str(spec.get("type", "unknown"))
            if spec.get("required") is True:
                required.append(str(name))
        else:
            types[str(name)] = "unknown"
    return {"names": names, "required": required, "types": types}


def node_summary(document: dict[str, Any]) -> tuple[list[str], list[str]]:
    raw = document.get("nodes")
    if not isinstance(raw, list):
        return [], []
    node_types = sorted({
        str(node.get("type"))
        for node in raw
        if isinstance(node, dict) and node.get("type")
    })
    integrations = sorted({
        node_type
        for node_type in node_types
        if any(marker.lower() in node_type.lower() for marker in INTEGRATION_NODE_MARKERS)
    })
    return node_types, integrations


def expected_path(document: dict[str, Any]) -> str:
    edges = document.get("edges")
    if not isinstance(edges, list) or not edges:
        return "node order / implicit graph"
    return f"declared graph with {len(edges)} edge records"


def load_preflight_reports(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("preflight report must be a JSON object keyed by workflow path")
    return value


def build_rows(
    root: Path,
    preflight_reports: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in workflow_paths(root):
        document = safe_document(path)
        stored_report = preflight_reports.get(path.as_posix())
        report = (
            stored_report
            if stored_report is not None
            else preflight_workflow_yaml(path.read_text(encoding="utf-8")).model_dump(mode="json")
        )
        inputs = input_summary(document)
        node_types, integrations = node_summary(document)
        kind = category(path)
        is_catalog = kind == "catalog" and path.parent == root
        valid = bool(report.get("valid"))
        issues = report.get("issues") if isinstance(report.get("issues"), list) else []
        errors = [issue for issue in issues if issue.get("severity") == "error"]
        warnings = [issue for issue in issues if issue.get("severity") == "warning"]
        base_result = "PASS" if valid and not warnings else "FAIL"
        issue_codes = [str(issue.get("code")) for issue in issues]

        for mode_id, mode_label in MODES:
            applicable = mode_id == "studio" and is_catalog
            if applicable:
                result = "PARTIAL" if base_result == "PASS" else "FAIL"
                reason = (
                    "Static schema/node/template/graph preflight passed; live execution, "
                    "workflow-specific output oracle, browser UI, recovery, and performance "
                    "were not executed."
                    if base_result == "PASS"
                    else "Workflow failed the warnings-as-errors static preflight gate."
                )
            else:
                result = "NOT_APPLICABLE"
                reason = (
                    "This top-level product mode does not execute workflows."
                    if mode_id != "studio"
                    else "Repository support/reference fixture; not listed by the product workflow library."
                )

            rows.append({
                "workflow_path": path.as_posix(),
                "workflow_file_id": path.stem,
                "workflow_name": str(document.get("name") or path.stem),
                "category": kind,
                "product_visible": is_catalog,
                "mode_id": mode_id,
                "mode_label": mode_label,
                "applicable": applicable,
                "inputs": inputs["names"],
                "required_inputs": inputs["required"],
                "input_types": inputs["types"],
                "expected_execution_path": expected_path(document),
                "expected_output": (
                    document.get("library", {}).get("outputs", [])
                    if isinstance(document.get("library"), dict)
                    else []
                ),
                "node_types": node_types,
                "external_or_service_dependencies": integrations,
                "relevant_ui_screens": (
                    ["Workflows", "Builder", "Cockpit", "Run History"]
                    if is_catalog else []
                ),
                "static_preflight": base_result,
                "preflight_valid": valid,
                "preflight_node_count": int(report.get("node_count") or 0),
                "preflight_error_count": len(errors),
                "preflight_warning_count": len(warnings),
                "preflight_issue_codes": issue_codes,
                "happy_path": "BLOCKED" if applicable else "NOT_APPLICABLE",
                "invalid_input": "BLOCKED" if applicable else "NOT_APPLICABLE",
                "boundary": "BLOCKED" if applicable else "NOT_APPLICABLE",
                "repeat_execution": "BLOCKED" if applicable else "NOT_APPLICABLE",
                "retry_recovery": "BLOCKED" if applicable else "NOT_APPLICABLE",
                "refresh_persistence": "BLOCKED" if applicable else "NOT_APPLICABLE",
                "ui": "BLOCKED" if applicable else "NOT_APPLICABLE",
                "performance": "BLOCKED" if applicable else "NOT_APPLICABLE",
                "result": result,
                "reason": reason,
            })
    return rows


def write_json(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            writer.writerow({
                key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value
                for key, value in row.items()
            })


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("workflows"))
    parser.add_argument("--preflight-json", type=Path)
    parser.add_argument("--json", type=Path, default=Path("qa-results/workflow-matrix.json"))
    parser.add_argument("--csv", type=Path, default=Path("qa-results/workflow-matrix.csv"))
    args = parser.parse_args()

    rows = build_rows(args.root, load_preflight_reports(args.preflight_json))
    if not rows:
        raise SystemExit("No workflow YAML files found")
    write_json(args.json, rows)
    write_csv(args.csv, rows)
    workflows = {row["workflow_path"] for row in rows}
    workflow_results = {
        path: next(row["static_preflight"] for row in rows if row["workflow_path"] == path)
        for path in workflows
    }
    print(json.dumps({
        "workflows": len(workflows),
        "matrix_rows": len(rows),
        "result_counts": Counter(row["result"] for row in rows),
        "workflow_static_preflight_counts": Counter(workflow_results.values()),
        "excluded_non_workflow_yaml": [
            "workflows/collections/default.yaml",
            "workflows/collections/proposal.yaml",
        ],
        "json": str(args.json),
        "csv": str(args.csv),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())