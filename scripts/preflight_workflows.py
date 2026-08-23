#!/usr/bin/env python3
"""Validate workflow YAML files without running nodes or calling an LLM."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from app.runtime.preflight import preflight_workflow_yaml


def _paths(values: list[str]) -> list[Path]:
    """Internal helper for the paths step.

    Args:
        values (list[str]): The values.

    Returns:
        list[Path]: The result.
    """
    if values:
        resolved: list[Path] = []
        for value in values:
            path = Path(value)
            if path.is_dir():
                resolved.extend(sorted(path.glob("*.yaml")))
                resolved.extend(sorted(path.glob("*.yml")))
            else:
                resolved.append(path)
        return list(dict.fromkeys(resolved))
    # Shipped library + the hidden reference corpus (generation exemplars):
    # both must stay preflight-clean, so both are in the default gate.
    return sorted(Path("workflows").glob("*.yaml")) + sorted(
        Path("workflows/reference").rglob("*.yaml")
    )


def main() -> int:
    """Compute the main.

    Returns:
        int: The result.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Dry-validate workflow YAML, node registration/config, models, "
            "templates, graph topology, and LangGraph compilation."
        )
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Workflow files or directories (default: workflows/*.yaml).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print complete machine-readable reports.",
    )
    parser.add_argument(
        "--warnings-as-errors",
        action="store_true",
        help="Return a failing exit code when warnings are present.",
    )
    args = parser.parse_args()

    paths = _paths(args.paths)
    if not paths:
        print("No workflow YAML files found.", file=sys.stderr)
        return 2

    failed = 0
    payload: dict[str, object] = {}
    for path in paths:
        if not path.exists():
            print(f"FAIL {path}: file not found")
            failed += 1
            continue
        report = preflight_workflow_yaml(path.read_text(encoding="utf-8"))
        payload[str(path)] = report.model_dump(mode="json")
        warning_failure = args.warnings_as_errors and bool(report.warnings)
        passed = report.valid and not warning_failure
        if not passed:
            failed += 1

        if not args.json:
            label = "PASS" if passed else "FAIL"
            print(
                f"{label} {path} — {report.node_count} nodes, "
                f"{len(report.errors)} errors, {len(report.warnings)} warnings, "
                f"{report.tokens_spent} tokens"
            )
            for issue in report.issues:
                where = issue.node_id or issue.path or "workflow"
                print(
                    f"  {issue.severity.value.upper()} "
                    f"{issue.code} [{where}] {issue.message}"
                )
                if issue.suggestion:
                    print(f"    Fix: {issue.suggestion}")

    if args.json:
        print(json.dumps(payload, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
