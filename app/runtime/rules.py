"""Deterministic business-rule evaluation.

This is the engine behind the Decision node, the Router's rule modes, and the
Builder's rule editor. Three properties matter and are why this is a module
rather than logic inside a node:

1.  **No LLM.** Evaluating a rule spends zero tokens and returns the same answer
    every time. Business logic that a person has to defend in a meeting does not
    belong in a prompt.
2.  **Explainable.** Every evaluation returns a trace of which conditions were
    checked, what value each one saw, and why the outcome followed. That trace is
    what the Builder's "why did this branch run?" view renders — the logic is
    inspectable rather than asserted.
3.  **Typed.** ``OPERATORS_BY_TYPE`` is the same table the UI reads to decide
    which operators to offer for a field, and the same table preflight reads to
    reject a rule that compares a list with ``>=``. One source, so the editor
    cannot construct a rule the runtime will refuse.

No ``eval``, no expression parsing: a condition is structured data
(field, operator, value), which is also what makes it round-trippable through
the visual editor.
"""
from __future__ import annotations

from typing import Any, Iterable, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


Operator = Literal[
    "equals",
    "not_equals",
    "contains",
    "not_contains",
    "greater_than",
    "less_than",
    "greater_or_equal",
    "less_or_equal",
    "exists",
    "does_not_exist",
    "is_empty",
    "is_not_empty",
    "in",
    "not_in",
    "is_true",
    "is_false",
]

#: Operators that ignore `value` entirely. The editor hides the value input for
#: these, and preflight does not type-check a value that is never read.
UNARY_OPERATORS: frozenset[str] = frozenset(
    {
        "exists",
        "does_not_exist",
        "is_empty",
        "is_not_empty",
        "is_true",
        "is_false",
    }
)

#: Operators whose `value` is a list of alternatives rather than one scalar.
SET_OPERATORS: frozenset[str] = frozenset({"in", "not_in"})

_NUMERIC_OPERATORS = (
    "equals",
    "not_equals",
    "greater_than",
    "less_than",
    "greater_or_equal",
    "less_or_equal",
    "exists",
    "does_not_exist",
    "in",
    "not_in",
)

_STRING_OPERATORS = (
    "equals",
    "not_equals",
    "contains",
    "not_contains",
    "exists",
    "does_not_exist",
    "is_empty",
    "is_not_empty",
    "in",
    "not_in",
)

_LIST_OPERATORS = (
    "contains",
    "not_contains",
    "is_empty",
    "is_not_empty",
    "exists",
    "does_not_exist",
)

#: Which operators are meaningful per field type. Keys match
#: app/runtime/field_schema.py's FieldKind so the Builder can look up a field's
#: operators straight from the upstream node's output contract (§39).
OPERATORS_BY_TYPE: dict[str, tuple[str, ...]] = {
    "string": _STRING_OPERATORS,
    "text": _STRING_OPERATORS,
    "date": _STRING_OPERATORS,
    "enum": (
        "equals",
        "not_equals",
        "in",
        "not_in",
        "exists",
        "does_not_exist",
        "is_empty",
        "is_not_empty",
    ),
    "number": _NUMERIC_OPERATORS,
    "integer": _NUMERIC_OPERATORS,
    "boolean": ("is_true", "is_false", "equals", "not_equals", "exists", "does_not_exist"),
    "list": _LIST_OPERATORS,
    "object": ("exists", "does_not_exist", "is_empty", "is_not_empty"),
    # Fallback for values whose type the Builder could not determine (a free-form
    # dict from a specialized node, say). Everything is offered; preflight can
    # only warn, not prove, for these.
    "unknown": tuple(sorted(set(_STRING_OPERATORS) | set(_NUMERIC_OPERATORS) | set(_LIST_OPERATORS))),
}


def operators_for_type(field_type: str) -> tuple[str, ...]:
    return OPERATORS_BY_TYPE.get(field_type, OPERATORS_BY_TYPE["unknown"])


OPERATOR_LABELS: dict[str, str] = {
    "equals": "equals",
    "not_equals": "does not equal",
    "contains": "contains",
    "not_contains": "does not contain",
    "greater_than": "is greater than",
    "less_than": "is less than",
    "greater_or_equal": "is at least",
    "less_or_equal": "is at most",
    "exists": "exists",
    "does_not_exist": "does not exist",
    "is_empty": "is empty",
    "is_not_empty": "is not empty",
    "in": "is one of",
    "not_in": "is not one of",
    "is_true": "is true",
    "is_false": "is false",
}


# --------------------------------------------------------------------------
# Path resolution
# --------------------------------------------------------------------------

_MISSING = object()


def resolve_path(context: dict[str, Any], path: str) -> Any:
    """Read a dotted path out of workflow state, returning ``_MISSING`` sentinel
    semantics as ``None`` for callers that don't care about the distinction.

    Accepts the same roots as the template resolver (``outputs.``, ``inputs.``,
    ``variables.``) plus the bare ``node_id.field`` shorthand, so a rule and a
    template address the same value the same way. Unlike the template resolver
    this never raises: a rule asking "does equipment.model exist" must be able to
    look at a path that isn't there — that is the whole point of the operator.
    """
    value = _resolve(context, path)
    return None if value is _MISSING else value


def path_exists(context: dict[str, Any], path: str) -> bool:
    return _resolve(context, path) is not _MISSING


def _resolve(context: dict[str, Any], path: str) -> Any:
    parts = [part for part in path.split(".") if part]
    if not parts:
        return _MISSING
    if parts[0] == "outputs":
        parts = ["node_outputs", *parts[1:]]
    elif parts[0] not in ("node_outputs", "inputs", "variables", "domain_state"):
        node_outputs = context.get("node_outputs") or {}
        if parts[0] in node_outputs:
            parts = ["node_outputs", *parts]

    cursor: Any = context
    for part in parts:
        if isinstance(cursor, dict):
            if part not in cursor:
                return _MISSING
            cursor = cursor[part]
            continue
        if isinstance(cursor, list):
            # A numeric segment indexes the list, matching the template engine's
            # behaviour so a rule and a template address integration results the
            # same way. Out of range resolves to missing, which every operator
            # already handles correctly.
            if part.lstrip("-").isdigit():
                index = int(part)
                if -len(cursor) <= index < len(cursor):
                    cursor = cursor[index]
                    continue
                return _MISSING

            # `items` addresses the element shape of a list of objects (the same
            # convention field_schema.field_paths emits), so a rule can ask about
            # a value inside a repeated structure. The list of those values is
            # returned, which makes `contains` the natural operator.
            if part == "items":
                continue
            collected = [
                item[part]
                for item in cursor
                if isinstance(item, dict) and part in item
            ]
            if not collected:
                return _MISSING
            cursor = collected
            continue
        return _MISSING
    return cursor


# --------------------------------------------------------------------------
# Condition / group model
# --------------------------------------------------------------------------

class Condition(BaseModel):
    """One leaf test: ``field operator value``."""

    model_config = ConfigDict(extra="forbid")

    field: str
    operator: Operator
    value: Any = None

    @model_validator(mode="after")
    def value_present_when_needed(self) -> "Condition":
        if self.operator in UNARY_OPERATORS:
            return self
        if self.operator in SET_OPERATORS:
            if not isinstance(self.value, (list, tuple)) or not self.value:
                raise ValueError(
                    f"operator {self.operator!r} on {self.field!r} needs a "
                    "non-empty list of alternatives"
                )
            return self
        if self.value is None:
            raise ValueError(
                f"operator {self.operator!r} on {self.field!r} needs a value"
            )
        return self


class ConditionGroup(BaseModel):
    """AND / OR / NOT over conditions and nested groups (§10)."""

    model_config = ConfigDict(extra="forbid")

    operator: Literal["and", "or", "not"] = "and"
    conditions: list[Union[Condition, "ConditionGroup"]] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def group_is_usable(self) -> "ConditionGroup":
        if not self.conditions:
            raise ValueError("a condition group must contain at least one condition")
        if self.operator == "not" and len(self.conditions) != 1:
            raise ValueError(
                "a NOT group must contain exactly one condition or group"
            )
        return self


ConditionGroup.model_rebuild()

# Pydantic resolves Union[Condition, ConditionGroup] left to right in smart
# mode; both have `extra="forbid"` and disjoint required fields, so a leaf can
# never be silently parsed as a group or vice versa.


class Action(BaseModel):
    """What a matched rule does.

    Deliberately small: a rule sets named business facts, it does not call
    anything. Side effects belong to integration nodes, where the autonomy
    boundary is visible on the canvas (§48).
    """

    model_config = ConfigDict(extra="forbid")

    field: str
    operation: Literal["set", "merge", "increase", "decrease"] = "set"
    value: Any = None

    @field_validator("operation", mode="before")
    @classmethod
    def _rename_legacy_append(cls, value: Any) -> Any:
        """`merge` is `append`'s new name — the accumulate-into-a-list
        behavior is unchanged, only the label. A rule authored (or saved to
        a live workflow) before the rename still says `append`; normalizing
        it here means every already-shipped decision-agent rule keeps
        working without a bulk rewrite of every saved workflow."""
        return "merge" if value == "append" else value

    @model_validator(mode="after")
    def value_matches_operation(self) -> "Action":
        if self.operation in ("increase", "decrease") and not isinstance(
            self.value, (int, float)
        ):
            raise ValueError(
                f"action {self.operation!r} on {self.field!r} needs a numeric value"
            )
        return self


class Rule(BaseModel):
    """One IF/THEN rule."""

    model_config = ConfigDict(extra="forbid")

    name: str
    when: ConditionGroup | None = None
    then: list[Action] = Field(default_factory=list)
    #: A rule with no `when` and `default: true` always fires — how the editor
    #: expresses "otherwise". Without the flag, an empty `when` is an authoring
    #: error rather than an accidental always-true rule.
    default: bool = False
    stop_on_match: bool = False
    description: str = ""

    @model_validator(mode="after")
    def rule_is_decidable(self) -> "Rule":
        if self.when is None and not self.default:
            raise ValueError(
                f"rule {self.name!r} needs conditions, or default: true to "
                "always apply"
            )
        if self.when is not None and self.default:
            raise ValueError(
                f"rule {self.name!r} cannot be a default rule and also have "
                "conditions"
            )
        if not self.then:
            raise ValueError(f"rule {self.name!r} has no actions")
        return self


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------

class ConditionTrace(BaseModel):
    """Why one condition passed or failed — rendered verbatim in the UI."""

    model_config = ConfigDict(extra="forbid")

    field: str
    operator: Operator
    expected: Any = None
    actual: Any = None
    matched: bool
    #: Set when the path was absent, so "failed because it is missing" reads
    #: differently from "failed because the value differs".
    missing: bool = False
    summary: str = ""


class GroupTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operator: Literal["and", "or", "not"]
    matched: bool
    children: list[Union[ConditionTrace, "GroupTrace"]] = Field(
        default_factory=list
    )


GroupTrace.model_rebuild()


class RuleTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    matched: bool
    default: bool = False
    trace: GroupTrace | None = None
    applied: list[dict[str, Any]] = Field(default_factory=list)
    description: str = ""


class RuleEvaluation(BaseModel):
    """Result of evaluating a rule set: the facts, plus the full reasoning."""

    model_config = ConfigDict(extra="forbid")

    values: dict[str, Any] = Field(default_factory=dict)
    matched_rules: list[str] = Field(default_factory=list)
    rules: list[RuleTrace] = Field(default_factory=list)

    def explanation_lines(self) -> list[str]:
        """Flat, human-readable "matched rules" list for the run trace (§24)."""
        lines: list[str] = []
        for rule in self.rules:
            if not rule.matched:
                continue
            marker = "default" if rule.default else "matched"
            lines.append(f"{rule.name} ({marker})")
            lines.extend(
                f"  ✓ {item.summary}"
                for item in _leaf_traces(rule.trace)
                if item.matched
            )
            lines.extend(
                f"  {item['field']} = {item['value']!r}" for item in rule.applied
            )
        return lines


def _leaf_traces(
    node: GroupTrace | ConditionTrace | None,
) -> Iterable[ConditionTrace]:
    if node is None:
        return []
    if isinstance(node, ConditionTrace):
        return [node]
    collected: list[ConditionTrace] = []
    for child in node.children:
        collected.extend(_leaf_traces(child))
    return collected


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, (str, bytes)):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def _contains(haystack: Any, needle: Any) -> bool:
    if haystack is None:
        return False
    if isinstance(haystack, (list, tuple, set)):
        # Case-insensitive for string members: a missing_information list from a
        # model may hold "Product_Model" where the rule says "product_model",
        # and treating those as different would silently disable the rule.
        if isinstance(needle, str):
            lowered = needle.strip().lower()
            return any(
                isinstance(item, str) and item.strip().lower() == lowered
                for item in haystack
            ) or needle in haystack
        return needle in haystack
    if isinstance(haystack, dict):
        return needle in haystack
    return str(needle).lower() in str(haystack).lower()


def _boolish(value: Any) -> bool | None:
    """Read a value as a boolean, or None if it isn't one.

    YAML `true` and the string "true" a model returned must compare equal —
    the rule editor writes a real boolean, but an extracted field commonly
    arrives as text.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "yes", "y"):
            return True
        if lowered in ("false", "no", "n"):
            return False
    return None


def _equals(actual: Any, expected: Any) -> bool:
    left_bool, right_bool = _boolish(actual), _boolish(expected)
    if left_bool is not None and right_bool is not None:
        return left_bool == right_bool
    if isinstance(actual, bool) or isinstance(expected, bool):
        # One side is a real boolean and the other is not boolean-like at all
        # (e.g. true == "critical"). Never coerce that into agreement.
        return False
    left, right = _as_number(actual), _as_number(expected)
    if left is not None and right is not None:
        return left == right
    if isinstance(actual, str) and isinstance(expected, str):
        return actual.strip().lower() == expected.strip().lower()
    return actual == expected


def _truthy(value: Any) -> bool:
    resolved = _boolish(value)
    if resolved is not None:
        return resolved
    return bool(value)


def evaluate_condition(
    condition: Condition, context: dict[str, Any]
) -> ConditionTrace:
    raw = _resolve(context, condition.field)
    missing = raw is _MISSING
    actual = None if missing else raw
    operator = condition.operator
    expected = condition.value

    if operator == "exists":
        matched = not missing and actual is not None
    elif operator == "does_not_exist":
        matched = missing or actual is None
    elif operator == "is_empty":
        matched = missing or _is_empty(actual)
    elif operator == "is_not_empty":
        matched = not missing and not _is_empty(actual)
    elif operator == "is_true":
        matched = not missing and _truthy(actual)
    elif operator == "is_false":
        matched = not missing and not _truthy(actual)
    elif missing:
        # Every remaining operator compares against a value. A missing path can
        # only fail them — including not_equals and not_contains, which would
        # otherwise "pass" for data that was never extracted and quietly route
        # incomplete requests as if they were complete.
        matched = False
    elif operator == "equals":
        matched = _equals(actual, expected)
    elif operator == "not_equals":
        matched = not _equals(actual, expected)
    elif operator == "contains":
        matched = _contains(actual, expected)
    elif operator == "not_contains":
        matched = not _contains(actual, expected)
    elif operator in ("greater_than", "less_than", "greater_or_equal", "less_or_equal"):
        left, right = _as_number(actual), _as_number(expected)
        if left is None or right is None:
            matched = False
        elif operator == "greater_than":
            matched = left > right
        elif operator == "less_than":
            matched = left < right
        elif operator == "greater_or_equal":
            matched = left >= right
        else:
            matched = left <= right
    elif operator == "in":
        matched = any(_equals(actual, option) for option in expected or [])
    elif operator == "not_in":
        matched = not any(_equals(actual, option) for option in expected or [])
    else:  # pragma: no cover - Operator Literal makes this unreachable
        raise ValueError(f"unsupported operator {operator!r}")

    return ConditionTrace(
        field=condition.field,
        operator=operator,
        expected=expected,
        actual=_previewable(actual),
        matched=matched,
        missing=missing,
        summary=_summarise(condition, actual, missing, matched),
    )


def _previewable(value: Any) -> Any:
    """Keep traces small — they are stored in run history and sent over SSE."""
    if isinstance(value, str) and len(value) > 200:
        return value[:200] + "…"
    if isinstance(value, dict):
        return {key: _previewable(item) for key, item in list(value.items())[:20]}
    if isinstance(value, (list, tuple)):
        return [_previewable(item) for item in list(value)[:20]]
    return value


def _summarise(
    condition: Condition, actual: Any, missing: bool, matched: bool
) -> str:
    label = OPERATOR_LABELS.get(condition.operator, condition.operator)
    if condition.operator in UNARY_OPERATORS:
        core = f"{condition.field} {label}"
    elif condition.operator in SET_OPERATORS:
        core = f"{condition.field} {label} {list(condition.value or [])}"
    else:
        core = f"{condition.field} {label} {condition.value!r}"
    if missing:
        return f"{core} — value not present"
    if matched:
        return core
    return f"{core} — actual {_previewable(actual)!r}"


def evaluate_group(
    group: ConditionGroup, context: dict[str, Any]
) -> GroupTrace:
    children: list[ConditionTrace | GroupTrace] = []
    for item in group.conditions:
        if isinstance(item, ConditionGroup):
            children.append(evaluate_group(item, context))
        else:
            children.append(evaluate_condition(item, context))

    results = [child.matched for child in children]
    if group.operator == "and":
        matched = all(results)
    elif group.operator == "or":
        matched = any(results)
    else:
        matched = not results[0]
    return GroupTrace(operator=group.operator, matched=matched, children=children)


def evaluate_rules(
    rules: list[Rule],
    context: dict[str, Any],
    *,
    initial: dict[str, Any] | None = None,
) -> RuleEvaluation:
    """Evaluate every rule in order and accumulate the facts they set.

    All rules are evaluated (unless one sets ``stop_on_match``) rather than
    first-match-wins, because business policy is usually additive: "escalate on
    low confidence" and "escalate on complaints" are two independent reasons that
    should both appear in the explanation when both hold.

    Conditions see values written by *earlier* rules — under the ``decisions.``
    root, which is how "IF production_stopped THEN urgency=critical" can be
    followed by "IF urgency = critical THEN notify" without an extra node.
    """
    values: dict[str, Any] = dict(initial or {})
    traces: list[RuleTrace] = []
    matched_names: list[str] = []

    for rule in rules:
        scoped = {**context, "decisions": values}
        if rule.default:
            matched, group_trace = True, None
        else:
            group_trace = evaluate_group(rule.when, scoped)  # type: ignore[arg-type]
            matched = group_trace.matched

        applied: list[dict[str, Any]] = []
        if matched:
            matched_names.append(rule.name)
            for action in rule.then:
                applied.append(_apply_action(action, values, scoped))

        traces.append(
            RuleTrace(
                name=rule.name,
                matched=matched,
                default=rule.default,
                trace=group_trace,
                applied=applied,
                description=rule.description,
            )
        )
        if matched and rule.stop_on_match:
            break

    return RuleEvaluation(values=values, matched_rules=matched_names, rules=traces)


def _apply_action(
    action: Action, values: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
    """Write one fact. Action values may themselves reference workflow state.

    A string action value of the form ``$path.to.field`` copies a live value
    instead of a constant — enough to express "set route from the classified
    intent" without a separate Transform node, while staying unambiguous against
    ordinary string constants.
    """
    resolved = action.value
    if isinstance(resolved, str) and resolved.startswith("$"):
        resolved = resolve_path(context, resolved[1:])

    current = values.get(action.field)
    if action.operation == "set":
        values[action.field] = resolved
    elif action.operation == "merge":
        existing = list(current) if isinstance(current, list) else (
            [] if current is None else [current]
        )
        existing.append(resolved)
        values[action.field] = existing
    else:
        base = _as_number(current) or 0.0
        delta = float(resolved or 0)
        total = base + delta if action.operation == "increase" else base - delta
        values[action.field] = int(total) if float(total).is_integer() else total

    return {
        "field": action.field,
        "operation": action.operation,
        "value": _previewable(values[action.field]),
    }


def collect_condition_fields(group: ConditionGroup | None) -> list[str]:
    """Every field path a group reads — used by preflight to check references."""
    if group is None:
        return []
    found: list[str] = []
    for item in group.conditions:
        if isinstance(item, ConditionGroup):
            found.extend(collect_condition_fields(item))
        else:
            found.append(item.field)
    return found


def collect_conditions(group: ConditionGroup | None) -> list[Condition]:
    """Every leaf condition in a group, for type checking in preflight."""
    if group is None:
        return []
    found: list[Condition] = []
    for item in group.conditions:
        if isinstance(item, ConditionGroup):
            found.extend(collect_conditions(item))
        else:
            found.append(item)
    return found
