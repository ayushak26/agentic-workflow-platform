"""Auto-fix preflight errors.

Two complementary repair strategies, applied in order by the caller (see
app/api/workflows.py's /workflows/autofix endpoint):

1. `apply_deterministic_fixes` — zero-token, pattern-matched patches for the
   mechanical/unambiguous error codes (typo'd template field, unknown node
   type with an obvious fuzzy match, unapproved model, etc). Every fix fails
   closed: if anything about an issue doesn't look exactly as expected, it is
   left untouched rather than guessed at.
2. `repair_with_llm` — for whatever remains, feeds the preflight errors back
   to the LLM as feedback alongside the current YAML and asks it to correct
   just those issues, re-validating and retrying up to a few times. Mirrors
   the self-correction loop in app/api/workflow_generation.py's
   run_generation_pipeline, minus the real-execution stage (autofix never
   runs the workflow for real).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from difflib import get_close_matches
import re
import types
import typing
from typing import Any, Awaitable, Callable

import yaml

from app.nodes.registry import NodeRegistry
from app.runtime.preflight import PreflightIssue, WorkflowPreflightReport
from app.runtime.schema import DEFAULT_LLM_MODELS
from app.runtime.templating import TEMPLATE_RE

# Codes that describe a backend/infra defect, not something a workflow YAML
# edit can ever fix — never attempted by either the deterministic fixer or
# the LLM repair loop.
NOT_AUTOFIXABLE_CODES: frozenset[str] = frozenset({
    "NODE_MODULE_IMPORT_FAILED",
    "NODE_CONTRACT_INVALID",
    "NODE_TYPE_NAME_MISMATCH",
    "NODE_SCHEMA_MISSING",
    "NODE_RUN_NOT_ASYNC",
    "REQUIRED_SERVICE_MISSING",
    "OBJECT_STORE_CREDENTIALS_INVALID",
    "OBJECT_STORE_UNAVAILABLE",
    "LOCAL_MODEL_UNAVAILABLE",
    "RUN_HISTORY_UNAVAILABLE",
    "MCP_SERVER_UNAVAILABLE",
    "MCP_SERVER_PROBE_FAILED",
    "MCP_TOOL_MISSING",
    "WEB_SEARCH_PROVIDER_UNAVAILABLE",
    "IMAGE_GENERATION_UNAVAILABLE",
    "KIMI_VISION_UNAVAILABLE",
    "MODEL_ACCESS_PROBE_FAILED",
    "MODEL_ACCESS_PROBE_INCOMPLETE",
    "MODEL_ACCESS_UNAVAILABLE",
    "AUTO_MODEL_ACCESS_UNAVAILABLE",
})


@dataclass
class DeterministicFixResult:
    yaml_text: str
    changed: bool
    fixes_applied: list[str] = field(default_factory=list)


def _get_path(raw: Any, path: str) -> Any:
    cursor = raw
    for part in path.split("."):
        cursor = cursor[int(part)] if isinstance(cursor, list) else cursor[part]
    return cursor


def _set_path(raw: Any, path: str, value: Any) -> None:
    parts = path.split(".")
    parent = _get_path(raw, ".".join(parts[:-1])) if len(parts) > 1 else raw
    last = parts[-1]
    if isinstance(parent, list):
        parent[int(last)] = value
    else:
        parent[last] = value


def _replace_template_reference(
    text: str, node_id: str, old_remainder: str, new_remainder: str,
) -> tuple[str, bool]:
    """Rewrite the field portion of a `{{node_id.old_remainder}}` (or
    `{{outputs.node_id.old_remainder}}`) reference to `new_remainder`,
    leaving everything else in the string untouched."""
    replaced = False

    def repl(match: re.Match[str]) -> str:
        nonlocal replaced
        parts = match.group(1).split(".")
        prefix_len = 1 if parts and parts[0] in ("outputs", "node_outputs") else 0
        body = parts[prefix_len:]
        if (
            len(body) >= 2
            and body[0] == node_id
            and ".".join(body[1:]) == old_remainder
        ):
            replaced = True
            new_parts = parts[:prefix_len] + [node_id] + new_remainder.split(".")
            return "{{" + ".".join(new_parts) + "}}"
        return match.group(0)

    return TEMPLATE_RE.sub(repl, text), replaced


def _replace_template_node_id(
    text: str, old_node_id: str, new_node_id: str,
) -> tuple[str, bool]:
    """Rewrite the node-id portion of a template reference, keeping whatever
    field path followed it."""
    replaced = False

    def repl(match: re.Match[str]) -> str:
        nonlocal replaced
        parts = match.group(1).split(".")
        prefix_len = 1 if parts and parts[0] in ("outputs", "node_outputs") else 0
        if len(parts) > prefix_len and parts[prefix_len] == old_node_id:
            replaced = True
            new_parts = parts[:prefix_len] + [new_node_id] + parts[prefix_len + 1:]
            return "{{" + ".".join(new_parts) + "}}"
        return match.group(0)

    return TEMPLATE_RE.sub(repl, text), replaced


def _node_by_id(raw: dict, node_id: str) -> dict | None:
    for node in raw.get("nodes") or []:
        if isinstance(node, dict) and node.get("id") == node_id:
            return node
    return None


def _fix_template_unknown_output_field(raw: dict, issue: PreflightIssue) -> str | None:
    if not issue.path or not issue.suggestion:
        return None
    message_match = re.match(
        r"^(\S+) '([^']+)' has no output field '([^']+)'\.$", issue.message,
    )
    fields_match = re.match(r"^Available fields: (.+)\.$", issue.suggestion)
    if not message_match or not fields_match:
        return None
    _node_type, referenced_node_id, bad_field = message_match.groups()
    available = [item.strip() for item in fields_match.group(1).split(",") if item.strip()]
    if len(available) == 1:
        new_field = available[0]
    else:
        close = get_close_matches(bad_field, available, n=1, cutoff=0.6)
        if len(close) != 1:
            return None
        new_field = close[0]

    try:
        text = _get_path(raw, issue.path)
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    if not isinstance(text, str):
        return None
    new_text, replaced = _replace_template_reference(
        text, referenced_node_id, bad_field, new_field,
    )
    if not replaced:
        return None
    _set_path(raw, issue.path, new_text)
    return (
        f"Replaced {{{{{referenced_node_id}.{bad_field}}}}} with "
        f"{{{{{referenced_node_id}.{new_field}}}}} at {issue.path} "
        f"(node {issue.node_id})."
    )


def _fix_template_unknown_structured_field(raw: dict, issue: PreflightIssue) -> str | None:
    if not issue.path:
        return None
    message_match = re.match(
        r"^(\S+) '([^']+)' does not declare structured field '([^']+)'\.$",
        issue.message,
    )
    if not message_match:
        return None
    node_type, referenced_node_id, bad_remainder = message_match.groups()
    referenced = _node_by_id(raw, referenced_node_id)
    if referenced is None:
        return None
    try:
        node_class = NodeRegistry.get(node_type)
    except KeyError:
        return None

    config = dict(referenced.get("config") or {})
    if referenced.get("selected_model"):
        config["model"] = referenced["selected_model"]
    fields = node_class.preflight_output_fields(config)

    prefix, _, bad_last = bad_remainder.rpartition(".")
    candidates = sorted({
        f.rsplit(".", 1)[-1] for f in fields if prefix and f.startswith(prefix + ".")
    })
    if len(candidates) == 1:
        new_last = candidates[0]
    else:
        close = get_close_matches(bad_last, candidates, n=1, cutoff=0.6)
        if len(close) != 1:
            return None
        new_last = close[0]
    new_remainder = f"{prefix}.{new_last}" if prefix else new_last

    try:
        text = _get_path(raw, issue.path)
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    if not isinstance(text, str):
        return None
    new_text, replaced = _replace_template_reference(
        text, referenced_node_id, bad_remainder, new_remainder,
    )
    if not replaced:
        return None
    _set_path(raw, issue.path, new_text)
    return (
        f"Replaced {{{{{referenced_node_id}.{bad_remainder}}}}} with "
        f"{{{{{referenced_node_id}.{new_remainder}}}}} at {issue.path} "
        f"(node {issue.node_id})."
    )


def _fix_template_unknown_node(raw: dict, issue: PreflightIssue) -> str | None:
    if not issue.path or not issue.suggestion:
        return None
    message_match = re.match(r"^Template references unknown node/path '(.+)'\.$", issue.message)
    suggestion_match = re.match(r"^Did you mean (.+)\?$", issue.suggestion)
    if not message_match or not suggestion_match:
        return None
    candidates = [item.strip() for item in suggestion_match.group(1).split(",") if item.strip()]
    if len(candidates) != 1:
        return None
    new_node_id = candidates[0]

    reference = message_match.group(1)
    ref_parts = reference.split(".")
    prefix_len = 1 if ref_parts and ref_parts[0] in ("outputs", "node_outputs") else 0
    if len(ref_parts) <= prefix_len:
        return None
    old_node_id = ref_parts[prefix_len]

    try:
        text = _get_path(raw, issue.path)
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    if not isinstance(text, str):
        return None
    new_text, replaced = _replace_template_node_id(text, old_node_id, new_node_id)
    if not replaced:
        return None
    _set_path(raw, issue.path, new_text)
    return f"Replaced unknown template node id {old_node_id!r} with {new_node_id!r} at {issue.path}."


def _fix_unknown_node_type(raw: dict, issue: PreflightIssue) -> str | None:
    if not issue.path or not issue.suggestion:
        return None
    suggestion_match = re.match(r"^Use one of: (.+)\.$", issue.suggestion)
    if not suggestion_match:
        return None
    candidates = [item.strip() for item in suggestion_match.group(1).split(",") if item.strip()]
    if len(candidates) != 1:
        return None
    try:
        old_type = _get_path(raw, issue.path)
        _set_path(raw, issue.path, candidates[0])
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    return f"Replaced unknown node type {old_type!r} with {candidates[0]!r} at {issue.path}."


def _fix_model_not_in_catalog(raw: dict, issue: PreflightIssue) -> str | None:
    if not issue.path:
        return None
    try:
        bad_model = _get_path(raw, issue.path)
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    if not isinstance(bad_model, str):
        return None
    close = get_close_matches(bad_model, DEFAULT_LLM_MODELS, n=1, cutoff=0.6)
    if close:
        new_model = close[0]
        guess = False
    else:
        new_model = DEFAULT_LLM_MODELS[0]
        guess = True
    try:
        _set_path(raw, issue.path, new_model)
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    if guess:
        return (
            f"Replaced unapproved model {bad_model!r} with default "
            f"{new_model!r} at {issue.path} — please confirm this is the "
            "intended model."
        )
    return f"Replaced unapproved model {bad_model!r} with {new_model!r} at {issue.path}."


def _fix_unknown_node_config_field(raw: dict, issue: PreflightIssue) -> str | None:
    if not issue.path:
        return None
    parts = issue.path.split(".")
    if len(parts) != 4 or parts[0] != "nodes" or parts[2] != "config":
        return None
    field_name = parts[3]
    try:
        index = int(parts[1])
        node = raw["nodes"][index]
        config = node.get("config")
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    if not isinstance(config, dict) or field_name not in config:
        return None
    try:
        node_class = NodeRegistry.get(node.get("type", ""))
    except KeyError:
        return None
    known_fields = list(node_class.config_schema.model_fields)
    close = get_close_matches(field_name, known_fields, n=1, cutoff=0.6)
    value = config.pop(field_name)
    if len(close) == 1:
        config[close[0]] = value
        return f"Renamed config field {field_name!r} to {close[0]!r} at {issue.path}."
    return f"Removed unrecognized config field {field_name!r} at {issue.path}."


def _accepts_plain_string(annotation: Any) -> bool:
    """Whether a pydantic field annotation permits an ordinary `str` — true
    for `str` itself and for `str | None`/`Optional[str]`."""
    if annotation is str:
        return True
    if typing.get_origin(annotation) in (typing.Union, types.UnionType):
        return any(_accepts_plain_string(arg) for arg in typing.get_args(annotation))
    return False


def _fix_dict_where_string_expected(raw: dict, issue: PreflightIssue) -> str | None:
    """A recurring, purely mechanical generation mistake: giving a field
    that only accepts one string a dict of several labelled values instead —
    e.g. AITaskAgent's single-string `input` field getting a dict of named
    upstream sources, which is what its separate `context` field is for.
    Flattens the dict into one readable multi-line string ("label: value"
    per line), preserving every original value — including any {{...}}
    template inside it, which still resolves normally once this is plain
    text — so the fix never discards information, only reshapes it.
    Declines for anything else NODE_CONFIG_INVALID might mean (a real enum
    mismatch, a missing required field, etc.) by simply not matching."""
    if not issue.path:
        return None
    parts = issue.path.split(".")
    if len(parts) != 4 or parts[0] != "nodes" or parts[2] != "config":
        return None
    field_name = parts[3]
    try:
        index = int(parts[1])
        node = raw["nodes"][index]
        config = node.get("config")
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    if not isinstance(config, dict) or field_name not in config:
        return None
    value = config[field_name]
    if not isinstance(value, dict):
        return None
    try:
        node_class = NodeRegistry.get(node.get("type", ""))
    except KeyError:
        return None
    field_info = node_class.config_schema.model_fields.get(field_name)
    if field_info is None or not _accepts_plain_string(field_info.annotation):
        return None
    config[field_name] = "\n".join(f"{key}: {item}" for key, item in value.items())
    return (
        f"Flattened {field_name!r} at {issue.path} from an object into a single "
        "string (that field only accepts one string value)."
    )


_SINGLE_ISSUE_FIXERS: dict[str, Callable[[dict, PreflightIssue], str | None]] = {
    "TEMPLATE_UNKNOWN_OUTPUT_FIELD": _fix_template_unknown_output_field,
    "TEMPLATE_UNKNOWN_STRUCTURED_FIELD": _fix_template_unknown_structured_field,
    "TEMPLATE_UNKNOWN_NODE": _fix_template_unknown_node,
    "UNKNOWN_NODE_TYPE": _fix_unknown_node_type,
    "MODEL_NOT_IN_CATALOG": _fix_model_not_in_catalog,
    "UNKNOWN_NODE_CONFIG_FIELD": _fix_unknown_node_config_field,
    "NODE_CONFIG_INVALID": _fix_dict_where_string_expected,
}


def apply_deterministic_fixes(
    yaml_text: str, report: WorkflowPreflightReport,
) -> DeterministicFixResult:
    try:
        raw = yaml.safe_load(yaml_text)
    except yaml.YAMLError:
        return DeterministicFixResult(yaml_text, changed=False)
    if not isinstance(raw, dict):
        return DeterministicFixResult(yaml_text, changed=False)

    fixes: list[str] = []
    duplicate_edge_indices: set[int] = set()

    for issue in report.errors:
        if issue.code in NOT_AUTOFIXABLE_CODES:
            continue
        if issue.code == "DUPLICATE_EDGE":
            if issue.path and issue.path.startswith("edges."):
                try:
                    duplicate_edge_indices.add(int(issue.path.split(".")[1]))
                except (IndexError, ValueError):
                    pass
            continue
        fixer = _SINGLE_ISSUE_FIXERS.get(issue.code)
        if fixer is None:
            continue
        try:
            description = fixer(raw, issue)
        except Exception:
            # Deterministic fixes must fail closed — leave the issue for the
            # LLM repair stage rather than risk a half-applied edit.
            description = None
        if description:
            fixes.append(description)

    edges = raw.get("edges")
    if duplicate_edge_indices and isinstance(edges, list):
        for index in sorted(duplicate_edge_indices, reverse=True):
            if 0 <= index < len(edges):
                del edges[index]
                fixes.append(f"Removed duplicate edge at edges.{index}.")

    if not fixes:
        return DeterministicFixResult(yaml_text, changed=False)

    new_yaml = yaml.safe_dump(raw, sort_keys=False, default_flow_style=False, allow_unicode=True)
    return DeterministicFixResult(new_yaml, changed=True, fixes_applied=fixes)


MAX_LLM_REPAIR_ATTEMPTS = 3
GENERIC_REPAIR_PROMPT = (
    "Fix the validation errors in this workflow YAML while preserving its "
    "structure and intent."
)


def format_preflight_feedback(report: WorkflowPreflightReport) -> str:
    return "; ".join(
        f"{issue.code} ({issue.node_id or issue.path or 'workflow'}): {issue.message}"
        for issue in report.errors
    )


@dataclass
class LlmRepairAttempt:
    success: bool
    detail: str


async def repair_with_llm(
    yaml_text: str,
    report: WorkflowPreflightReport,
    *,
    static_check: Callable[[str], Awaitable[WorkflowPreflightReport]],
    generate_yaml: Callable[[str, str | None, str | None], Awaitable[str]],
) -> tuple[str, WorkflowPreflightReport, list[LlmRepairAttempt]]:
    """Feed remaining preflight errors back to the LLM up to
    MAX_LLM_REPAIR_ATTEMPTS times. Mirrors run_generation_pipeline's static
    loop (app/api/workflow_generation.py), minus the real-execution stage."""
    attempts: list[LlmRepairAttempt] = []
    current_yaml = yaml_text
    current_report = report

    for _ in range(MAX_LLM_REPAIR_ATTEMPTS):
        if current_report.valid:
            break
        feedback = (
            "Your previous YAML failed static validation with these "
            f"issues: {format_preflight_feedback(current_report)}. Return a "
            "corrected, complete YAML."
        )
        current_yaml = await generate_yaml(GENERIC_REPAIR_PROMPT, current_yaml, feedback)
        current_report = await static_check(current_yaml)
        attempts.append(LlmRepairAttempt(
            success=current_report.valid,
            detail="Preflight passed." if current_report.valid else format_preflight_feedback(current_report),
        ))

    return current_yaml, current_report, attempts
