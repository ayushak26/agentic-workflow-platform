"""RouterAgent: the workflow's branching primitive.

The compiler's conditional edge reads ``node_outputs[router_id]["route"]``.
This node's job is to write that field correctly, and — just as importantly —
to record *why* it chose that value.

Four modes, one node type:

    field        route on the value of one field, with a branch per value
                 (the common case: intent → department)
    conditions   route on the first matching rule group, for routes that
                 depend on several facts at once
    rule         legacy string-expression rules ("a.b == 'x'"), kept for
                 existing workflows
    llm          ask a model to choose, for genuinely fuzzy routing

`field` and `conditions` are deterministic, cost nothing, and produce an
explanation the Builder renders directly. A new department, label, or threshold
is a configuration change in any of them — never a new node type.
"""
from __future__ import annotations

import operator
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.runtime.rules import (
    ConditionGroup,
    GroupTrace,
    evaluate_group,
    resolve_path,
)


class RouteRule(BaseModel):
    """One legacy rule. The first matching rule wins. `default` matches anything."""
    name: str
    condition: str | None = None                # e.g. "rfp_intel.parsed.industry == 'finance'"
    default: bool = False


class RouteCase(BaseModel):
    """One deterministic branch in `conditions` mode.

    The route name is the branch label that appears on the canvas edge, so the
    graph reads as the business process rather than as a set of node ids.
    """

    model_config = ConfigDict(extra="forbid")

    route: str
    when: ConditionGroup | None = None
    description: str = ""

    @model_validator(mode="after")
    def case_is_decidable(self) -> "RouteCase":
        if self.when is None:
            raise ValueError(
                f"route case {self.route!r} needs conditions; use `fallback` for "
                "the otherwise branch"
            )
        return self


class RouterConfig(BaseModel):
    mode: Literal["field", "conditions", "rule", "llm"] = "rule"

    # field mode
    #: The value to branch on, e.g. "outputs.understand.result.intent".
    route_field: str | None = None
    #: value → route name. A route name may be reused by several values, which is
    #: how "complaint" and "warranty_claim" both reach Customer Service without
    #: duplicating a branch on the canvas.
    branches: dict[str, str] = Field(default_factory=dict)

    # conditions mode
    cases: list[RouteCase] = Field(default_factory=list)

    #: Used by both deterministic modes when nothing matches. A router without a
    #: fallback can fail mid-run on an unexpected value, so preflight reports a
    #: missing one (MISSING_DEFAULT_ROUTE).
    fallback: str | None = None

    # rule mode (legacy) and llm mode
    rules: list[RouteRule] = Field(default_factory=list)
    model: str | None = None
    prompt: str | None = None                   # asks the model to pick a route name
    context: str | None = None                  # context passed to the LLM (templated)

    @model_validator(mode="after")
    def mode_has_what_it_needs(self) -> "RouterConfig":
        if self.mode == "field":
            if not self.route_field:
                raise ValueError("field mode needs a route_field")
            if not self.branches:
                raise ValueError("field mode needs at least one branch")
        elif self.mode == "conditions":
            if not self.cases:
                raise ValueError("conditions mode needs at least one case")
        elif not self.rules:
            raise ValueError(f"{self.mode} mode needs at least one rule")
        return self

    def route_names(self) -> list[str]:
        """Every route this router can emit — read by preflight to compare
        against the edge's declared branches, and by the Builder to draw them."""
        names: list[str] = []
        if self.mode == "field":
            names.extend(self.branches.values())
        elif self.mode == "conditions":
            names.extend(case.route for case in self.cases)
        else:
            names.extend(rule.name for rule in self.rules)
        if self.fallback:
            names.append(self.fallback)
        return list(dict.fromkeys(names))


class RouterOutput(BaseModel):
    route: str
    reason: str | None = None
    #: The value the decision was based on, in `field` mode. Shown in the run
    #: trace so "why Technical Support?" is answerable without re-reading state.
    route_value: Any = None
    #: Per-condition trace for `conditions` mode; empty for the other modes.
    explanation: list[dict[str, Any]] = Field(default_factory=list)
    matched_conditions: list[str] = Field(default_factory=list)
    #: True when no branch matched and the fallback was used — the single most
    #: useful signal when a live demo routes somewhere unexpected.
    used_fallback: bool = False


class RouterInput(BaseModel):
    pass


_SAFE_OPS = {
    "==": operator.eq, "!=": operator.ne,
    "<":  operator.lt, "<=": operator.le,
    ">":  operator.gt, ">=": operator.ge,
}


@NodeRegistry.register
class RouterAgent(NodeType):
    type_name = "RouterAgent"
    description = (
        "Branch the workflow on a field value, on business conditions, or via "
        "model judgment — with the reason recorded."
    )
    input_schema = RouterInput
    output_schema = RouterOutput
    config_schema = RouterConfig

    family: ClassVar[str] = "core"
    execution_kind: ClassVar[str] = "deterministic"
    about: ClassVar[dict[str, Any]] = {
        "what": (
            "Chooses one outgoing branch. In field mode it maps one value to a "
            "branch; in conditions mode the first matching rule group wins."
        ),
        "why": (
            "Routing is where a business process becomes visible. Branch labels "
            "appear on the canvas edges, and the chosen branch carries its reason."
        ),
        "receives": "A classified value or business facts from upstream nodes.",
        "produces": "route (the branch taken), the value behind it, and the matched conditions.",
        "uses_ai": False,
        "external_action": False,
        "presets": [
            {
                "id": "field_routing",
                "label": "Route on a field",
                "summary": "One branch per value of a classified field.",
                "config": {"mode": "field"},
            },
            {
                "id": "condition_routing",
                "label": "Route on conditions",
                "summary": "Branch on several business facts at once.",
                "config": {"mode": "conditions"},
            },
            {
                "id": "llm_routing",
                "label": "Route by model judgment",
                "summary": "Only for genuinely fuzzy routing; costs tokens.",
                "config": {"mode": "llm"},
            },
        ],
    }

    @classmethod
    def required_services(cls, config: dict[str, Any]) -> set[str]:
        if config.get("mode", "rule") == "llm":
            return {"llm", "cost_ledger"}
        return set()

    async def run(self, state, resolved_config: dict[str, Any]) -> dict[str, Any]:
        cfg = RouterConfig(**resolved_config)
        if cfg.mode == "field":
            return self._route_by_field(cfg, state)
        if cfg.mode == "conditions":
            return self._route_by_conditions(cfg, state)
        if cfg.mode == "rule":
            return self._route_by_rule(cfg, state)
        return await self._route_by_llm(cfg)

    # -- deterministic: one field, one branch per value -----------------

    def _route_by_field(self, cfg: RouterConfig, state: dict) -> dict[str, Any]:
        raw = resolve_path(dict(state), cfg.route_field or "")
        key = _branch_key(raw)
        # Case-insensitive match so a model returning "Technical_Support" still
        # reaches the branch an author declared as "technical_support".
        lookup = {name.strip().lower(): route for name, route in cfg.branches.items()}
        route = lookup.get(key.strip().lower()) if key else None

        if route is not None:
            return {
                "route": route,
                "reason": f"{cfg.route_field} = {raw!r}",
                "route_value": raw,
                "explanation": [
                    {
                        "field": cfg.route_field,
                        "operator": "equals",
                        "expected": key,
                        "actual": raw,
                        "matched": True,
                        "summary": f"{cfg.route_field} = {raw!r} → {route}",
                    }
                ],
                "matched_conditions": [f"{cfg.route_field} = {raw!r}"],
                "used_fallback": False,
            }

        if not cfg.fallback:
            raise ValueError(
                f"Router {self.node_id}: {cfg.route_field} = {raw!r} matches no "
                f"branch ({sorted(cfg.branches)}) and no fallback branch is "
                "configured"
            )
        return {
            "route": cfg.fallback,
            "reason": (
                f"{cfg.route_field} = {raw!r} matched no branch; used the "
                f"fallback branch"
            ),
            "route_value": raw,
            "explanation": [
                {
                    "field": cfg.route_field,
                    "operator": "in",
                    "expected": sorted(cfg.branches),
                    "actual": raw,
                    "matched": False,
                    "summary": (
                        f"{cfg.route_field} = {raw!r} is not a declared branch "
                        f"value → {cfg.fallback}"
                    ),
                }
            ],
            "matched_conditions": [],
            "used_fallback": True,
        }

    # -- deterministic: first matching condition group ------------------

    def _route_by_conditions(self, cfg: RouterConfig, state: dict) -> dict[str, Any]:
        context = dict(state)
        traces: list[dict[str, Any]] = []
        for case in cfg.cases:
            trace = evaluate_group(case.when, context)  # type: ignore[arg-type]
            traces.append({"route": case.route, **trace.model_dump()})
            if trace.matched:
                matched = _matched_summaries(trace)
                return {
                    "route": case.route,
                    "reason": case.description or "; ".join(matched),
                    "route_value": None,
                    "explanation": traces,
                    "matched_conditions": matched,
                    "used_fallback": False,
                }

        if not cfg.fallback:
            raise ValueError(
                f"Router {self.node_id}: no case matched and no fallback branch "
                "is configured"
            )
        return {
            "route": cfg.fallback,
            "reason": "no case matched; used the fallback branch",
            "route_value": None,
            "explanation": traces,
            "matched_conditions": [],
            "used_fallback": True,
        }

    # -- legacy string-expression rules --------------------------------

    def _route_by_rule(self, cfg, state):
        for rule in cfg.rules:
            if rule.default:
                continue
            if self._eval_condition(rule.condition, state):
                return {
                    "route": rule.name,
                    "reason": f"rule '{rule.condition}' matched",
                    "matched_conditions": [str(rule.condition)],
                }
        # Fallback to default
        for rule in cfg.rules:
            if rule.default:
                return {
                    "route": rule.name,
                    "reason": "default route",
                    "used_fallback": True,
                }
        raise ValueError(f"Router {self.node_id} matched no rule and has no default")

    def _eval_condition(self, condition: str | None, state: dict) -> bool:
        """Safe expression eval — supports only `path OP literal`.
        No arbitrary Python. Structured `conditions` mode is the supported way to
        express AND/OR/NOT; this stays as-is for existing workflows.

        Path resolution goes through the same `resolve_path` every other mode
        uses — it returns None for a path that isn't there instead of raising,
        which matters a lot here: an upstream lookup that legitimately found
        nothing (an unmatched customer, an empty ownership record) must lose
        the comparison, not crash the run three nodes downstream."""
        if not condition:
            return False
        for op_str, op_fn in _SAFE_OPS.items():
            if f" {op_str} " in condition:
                lhs, rhs = condition.split(f" {op_str} ", 1)
                lhs_val = resolve_path(state, lhs.strip())
                rhs_val = self._parse_literal(rhs.strip())
                if lhs_val is None:
                    # A missing path is not the same fact as "equal to the
                    # empty/zero value", but comparing against one is the only
                    # way this grammar has to ask "is this field populated" —
                    # `owner_name != ''` means exactly that. Normalizing None
                    # to the literal's own empty value makes every operator
                    # answer that consistently instead of raising (mismatched
                    # types) or silently miscounting "missing" as "present"
                    # (bare `!=`, which None fails against everything).
                    lhs_val = type(rhs_val)() if rhs_val is not None else None
                return op_fn(lhs_val, rhs_val)
        raise ValueError(f"Unparseable condition: {condition!r}")

    @staticmethod
    def _parse_literal(s: str) -> Any:
        s = s.strip()
        if s.startswith(("'", '"')) and s.endswith(("'", '"')):
            return s[1:-1]
        if s in ("true", "True"):  return True
        if s in ("false", "False"): return False
        if s in ("null", "None"):  return None
        try:    return int(s)
        except ValueError: pass
        try:    return float(s)
        except ValueError: pass
        return s

    async def _route_by_llm(self, cfg):
        llm = self.services["llm"]
        route_names = [r.name for r in cfg.rules]
        prompt = (
            f"{cfg.prompt}\n\nContext:\n{cfg.context}\n\n"
            f"Choose ONE route from: {route_names}\n"
            f"Respond with only the route name, nothing else."
        )
        response = await llm.complete(
            model=cfg.model or "claude-sonnet-4-5",
            system=(
                "Route the supplied context to exactly one allowed route. "
                "Return only the route name."
            ),
            user=prompt,
            temperature=0.0,
            max_tokens=32,
        )
        raw = response.text
        choice = raw.strip().strip('"').strip("'")
        if choice not in route_names:
            # Fall back to default rule
            for r in cfg.rules:
                if r.default:
                    return {
                        "route": r.name,
                        "reason": f"LLM returned invalid '{choice}', defaulted",
                        "used_fallback": True,
                    }
            raise ValueError(f"LLM returned unknown route: {choice!r}")
        return {"route": choice, "reason": "LLM judgment"}


def _branch_key(value: Any) -> str:
    """Normalise a routed value into a branch key.

    Booleans map to "true"/"false" so a boolean field can drive two branches
    without a Transform in between; everything else is compared as text, because
    branch keys come from YAML mapping keys, which are always strings.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)


def _matched_summaries(trace: GroupTrace) -> list[str]:
    collected: list[str] = []
    for child in trace.children:
        if isinstance(child, GroupTrace):
            if child.matched:
                collected.extend(_matched_summaries(child))
        elif child.matched:
            collected.append(child.summary)
    return collected
