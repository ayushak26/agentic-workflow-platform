"""RouterAgent: the workflow's branching primitive.

The compiler's conditional edge reads ``node_outputs[router_id]["route"]``
(single selection) or ``["routes"]`` (multi selection). This node's job is to
write those fields correctly, and — just as importantly — to record *why* it
chose them.

Four modes, one node type — *how* a branch is decided:

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

Orthogonal to `mode` is `selection` — *how many* branches fire:

    single (default)   choose exactly one — everything above describes this.
    multi               Multi-Route: choose one or more from the same
                         evaluation, and execute all of them in parallel.
                         Supported by field, conditions, and llm mode (not
                         rule, which stays first-match-wins). A Multi-Route's
                         selected branches must each run to their own
                         terminal — nothing may reconverge downstream of two
                         or more of them, since a branch that wasn't selected
                         never runs and a hard join waiting on it would never
                         fire. Preflight's MULTIROUTE_ANDJOIN_MAY_NOT_FIRE
                         check rejects that shape before it can be saved;
                         aggregate results in the workflow's `output:`
                         section instead, which already tolerates a branch
                         that didn't run.
"""
from __future__ import annotations

import operator
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.runtime.state import WorkflowState

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
        """Compute the case is decidable.

        Returns:
            'RouteCase': The is decidable.
        """
        if self.when is None:
            raise ValueError(
                f"route case {self.route!r} needs conditions; use `fallback` for "
                "the otherwise branch"
            )
        return self


class RouterConfig(BaseModel):
    """Pydantic model defining the RouterConfig shape.

    Attributes:
        mode (Literal['field', 'conditions', 'rule', 'llm']).
        selection (Literal['single', 'multi']).
        route_field (str | None).
        branches (dict[str, str]).
        cases (list[RouteCase]).
        fallback (str | None).
        rules (list[RouteRule]).
        model (str | None).
    """
    mode: Literal["field", "conditions", "rule", "llm"] = Field(
        default="rule",
        description="How this step decides a branch: field (map a value to a branch), conditions (first matching rule group), rule (legacy expressions), or llm (ask a model).",
    )
    #: Orthogonal to `mode` — `mode` is *how* a branch is decided, `selection`
    #: is *how many* fire. Default "single" keeps every existing workflow
    #: byte-identical. "multi" is Multi-Route: several relevant branches
    #: (e.g. Sales AND Engineering AND Supply Chain) selected from one
    #: evaluation and executed in parallel, instead of exactly one.
    selection: Literal["single", "multi"] = Field(
        default="single",
        description="How many branches this step can take at once: single (choose one, the default) or multi (choose one or more — Multi-Route).",
    )

    # field mode
    #: The value to branch on, e.g. "outputs.understand.result.intent".
    route_field: str | None = Field(
        default=None,
        description="The value to branch on, in field mode — e.g. an upstream step's classified intent.",
    )
    #: value → route name. A route name may be reused by several values, which is
    #: how "complaint" and "warranty_claim" both reach Customer Service without
    #: duplicating a branch on the canvas.
    branches: dict[str, str] = Field(
        default_factory=dict,
        description="value → route name, in field mode. Several values may share one route name.",
    )

    # conditions mode
    cases: list[RouteCase] = Field(
        default_factory=list,
        description="Ordered rule groups, in conditions mode — the first matching group's route wins.",
    )

    #: Used by both deterministic modes when nothing matches. A router without a
    #: fallback can fail mid-run on an unexpected value, so preflight reports a
    #: missing one (MISSING_DEFAULT_ROUTE).
    fallback: str | None = Field(
        default=None,
        description="Route used when nothing else matches. Leaving this unset can fail a run on an unexpected value — preflight flags a missing fallback.",
    )

    # rule mode (legacy) and llm mode
    rules: list[RouteRule] = Field(default_factory=list, description="Legacy string-expression rules, in rule mode.")
    model: str | None = Field(default=None, description="Which model chooses the route, in llm mode.")
    prompt: str | None = Field(default=None, description="What to ask the model when picking a route, in llm mode.")
    context: str | None = Field(default=None, description="Templated context passed to the model, in llm mode.")

    @model_validator(mode="after")
    def mode_has_what_it_needs(self) -> "RouterConfig":
        """Compute the mode has what it needs.

        Returns:
            'RouterConfig': The has what it needs.
        """
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

    @model_validator(mode="after")
    def multi_selection_not_supported_in_rule_mode(self) -> "RouterConfig":
        # `rule` is legacy string-expression matching, kept as-is for existing
        # workflows — its first-match-wins loop (_route_by_rule) is not worth
        # the risk of changing to accumulate. field/conditions/llm all have a
        # well-defined "evaluate everything, collect every match" reading.
        """Compute the multi selection not supported in rule mode.

        Returns:
            'RouterConfig': The selection not supported in rule mode.
        """
        if self.selection == "multi" and self.mode == "rule":
            raise ValueError(
                "multi selection is not supported in rule mode — use field, "
                "conditions, or llm mode for Multi-Route"
            )
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
    #: In single selection: the one branch taken. In multi selection: the
    #: first selected branch, kept populated so anything reading `.route`
    #: (run inspectors, a `{{router.route}}` reference
    #: written before Multi-Route existed) still renders something
    #: meaningful — `.route` is display-only in multi mode, `.routes` is
    #: authoritative there.
    """Pydantic model defining the RouterOutput shape.

    Attributes:
        route (str).
        routes (list[str]).
        reason (str | None).
        route_value (Any).
        explanation (list[dict[str, Any]]).
        matched_conditions (list[str]).
        used_fallback (bool).
    """
    route: str = ""
    #: Every branch selected — always exactly one entry in single selection
    #: (mirroring `route`), the full selection in multi. This is the field
    #: the compiler's multi-route dispatch actually reads.
    routes: list[str] = Field(default_factory=list)
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
    """Pydantic model defining the RouterInput shape."""
    pass


_SAFE_OPS = {
    "==": operator.eq, "!=": operator.ne,
    "<":  operator.lt, "<=": operator.le,
    ">":  operator.gt, ">=": operator.ge,
}


@NodeRegistry.register
class RouterAgent(NodeType):
    """Workflow node type implementing the RouterAgent capability.

    Attributes:
        family (ClassVar[str]).
        execution_kind (ClassVar[str]).
        about (ClassVar[dict[str, Any]]).
    """
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
            "Chooses one outgoing branch (or, in Multi-Route selection, "
            "every branch that applies). In field mode it maps a value to a "
            "branch; in conditions mode each matching rule group's branch "
            "is taken."
        ),
        "why": (
            "Routing is where a business process becomes visible. Branch labels "
            "appear on the canvas edges, and the chosen branch carries its reason."
        ),
        "receives": "A classified value or business facts from upstream nodes.",
        "produces": "route/routes (the branch(es) taken), the value behind it, and the matched conditions.",
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
            {
                "id": "multi_routing",
                "label": "Multi-Route: several branches at once",
                "summary": "Select every relevant branch (e.g. Sales AND Engineering AND Supply Chain) from one evaluation.",
                "config": {"mode": "conditions", "selection": "multi"},
            },
        ],
    }

    @classmethod
    def required_services(cls, config: dict[str, Any]) -> set[str]:
        """Compute the required services.

        Args:
            config (dict[str, Any]): Node configuration mapping.

        Returns:
            set[str]: The services.
        """
        if config.get("mode", "rule") == "llm":
            return {"llm", "cost_ledger"}
        return set()

    async def run(self, state, resolved_config: dict[str, Any]) -> dict[str, Any]:
        """Run the result.

        Args:
            state: Current workflow state.
            resolved_config (dict[str, Any]): Configuration after template resolution.

        Returns:
            dict[str, Any]: The result.
        """
        cfg = RouterConfig(**resolved_config)
        if cfg.selection == "multi":
            if cfg.mode == "field":
                return self._route_by_field_multi(cfg, state)
            if cfg.mode == "conditions":
                return self._route_by_conditions_multi(cfg, state)
            return await self._route_by_llm_multi(cfg)

        if cfg.mode == "field":
            result = self._route_by_field(cfg, state)
        elif cfg.mode == "conditions":
            result = self._route_by_conditions(cfg, state)
        elif cfg.mode == "rule":
            result = self._route_by_rule(cfg, state)
        else:
            result = await self._route_by_llm(cfg)
        # Single selection always populates `routes` too, so the compiler's
        # multi-route dispatch and anything else reading `.routes` sees a
        # consistent shape regardless of which selection mode produced it.
        result.setdefault("routes", [result["route"]])
        return result

    # -- deterministic: one field, one branch per value -----------------

    def _route_by_field(self, cfg: RouterConfig, state: WorkflowState) -> dict[str, Any]:
        """Internal helper for the route by field step.

        Args:
            cfg (RouterConfig): The cfg.
            state (dict): Current workflow state.

        Returns:
            dict[str, Any]: The by field.
        """
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

    def _route_by_field_multi(self, cfg: RouterConfig, state: WorkflowState) -> dict[str, Any]:
        """Multi-Route, field mode: `route_field` resolves to a *list* of
        values (e.g. a Transform's classified `needs: [...]`), each mapped
        through `branches` the same way single-selection does — every match
        fires, not just the first."""
        raw = resolve_path(dict(state), cfg.route_field or "")
        values = raw if isinstance(raw, list) else ([raw] if raw is not None else [])
        lookup = {name.strip().lower(): route for name, route in cfg.branches.items()}

        routes: list[str] = []
        explanation: list[dict[str, Any]] = []
        matched_conditions: list[str] = []
        for value in values:
            key = _branch_key(value)
            route = lookup.get(key.strip().lower()) if key else None
            if route is None:
                continue
            if route not in routes:
                routes.append(route)
            matched_conditions.append(f"{cfg.route_field} = {value!r}")
            explanation.append({
                "field": cfg.route_field,
                "operator": "equals",
                "expected": key,
                "actual": value,
                "matched": True,
                "summary": f"{cfg.route_field} = {value!r} → {route}",
            })

        if routes:
            return {
                "route": routes[0],
                "routes": routes,
                "reason": f"{cfg.route_field} = {raw!r}",
                "route_value": raw,
                "explanation": explanation,
                "matched_conditions": matched_conditions,
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
            "routes": [cfg.fallback],
            "reason": (
                f"{cfg.route_field} = {raw!r} matched no branch; used the "
                f"fallback branch"
            ),
            "route_value": raw,
            "explanation": [{
                "field": cfg.route_field,
                "operator": "in",
                "expected": sorted(cfg.branches),
                "actual": raw,
                "matched": False,
                "summary": (
                    f"{cfg.route_field} = {raw!r} matched no declared branch "
                    f"value → {cfg.fallback}"
                ),
            }],
            "matched_conditions": [],
            "used_fallback": True,
        }

    # -- deterministic: first matching condition group ------------------

    def _route_by_conditions(self, cfg: RouterConfig, state: WorkflowState) -> dict[str, Any]:
        """Internal helper for the route by conditions step.

        Args:
            cfg (RouterConfig): The cfg.
            state (dict): Current workflow state.

        Returns:
            dict[str, Any]: The by conditions.
        """
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

    def _route_by_conditions_multi(
        self, cfg: RouterConfig, state: WorkflowState,
    ) -> dict[str, Any]:
        """Multi-Route, conditions mode: every case is evaluated (never stop
        at the first match) and every matching route is collected — business
        policy here is additive, the same reasoning DecisionAgent's rule
        engine already applies (app/runtime/rules.py)."""
        context = dict(state)
        traces: list[dict[str, Any]] = []
        routes: list[str] = []
        matched_conditions: list[str] = []
        reasons: list[str] = []
        for case in cfg.cases:
            trace = evaluate_group(case.when, context)  # type: ignore[arg-type]
            traces.append({"route": case.route, **trace.model_dump()})
            if not trace.matched:
                continue
            if case.route not in routes:
                routes.append(case.route)
            matched = _matched_summaries(trace)
            matched_conditions.extend(matched)
            reasons.append(case.description or "; ".join(matched) or case.route)

        if routes:
            return {
                "route": routes[0],
                "routes": routes,
                "reason": "; ".join(dict.fromkeys(reasons)),
                "route_value": None,
                "explanation": traces,
                "matched_conditions": matched_conditions,
                "used_fallback": False,
            }

        if not cfg.fallback:
            raise ValueError(
                f"Router {self.node_id}: no case matched and no fallback branch "
                "is configured"
            )
        return {
            "route": cfg.fallback,
            "routes": [cfg.fallback],
            "reason": "no case matched; used the fallback branch",
            "route_value": None,
            "explanation": traces,
            "matched_conditions": [],
            "used_fallback": True,
        }

    # -- legacy string-expression rules --------------------------------

    def _route_by_rule(self, cfg, state):
        """Internal helper for the route by rule step.

        Args:
            cfg: The cfg.
            state: Current workflow state.
        """
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
        """Parse the literal.

        Args:
            s (str): The s.

        Returns:
            Any: The literal.
        """
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
        """Internal helper for the route by llm step.

        Args:
            cfg: The cfg.
        """
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

    async def _route_by_llm_multi(self, cfg: RouterConfig) -> dict[str, Any]:
        """Multi-Route, llm mode: ask the model to choose all routes that
        apply and parse a comma-separated list back — for genuinely fuzzy
        "which of these departments does this enquiry touch" judgment."""
        llm = self.services["llm"]
        route_names = [r.name for r in cfg.rules]
        prompt = (
            f"{cfg.prompt}\n\nContext:\n{cfg.context}\n\n"
            f"Choose EVERY route that applies, from: {route_names}\n"
            "Respond with only the matching route names, comma-separated, "
            "nothing else. If none apply, respond with an empty line."
        )
        response = await llm.complete(
            model=cfg.model or "claude-sonnet-4-5",
            system=(
                "Route the supplied context to every route that genuinely "
                "applies — zero, one, or several. Return only a "
                "comma-separated list of route names, nothing else."
            ),
            user=prompt,
            temperature=0.0,
            max_tokens=64,
        )
        raw = response.text
        candidates = [item.strip().strip('"').strip("'") for item in raw.split(",")]
        routes = [name for name in dict.fromkeys(candidates) if name in route_names]

        if routes:
            return {
                "route": routes[0],
                "routes": routes,
                "reason": "LLM judgment",
                "used_fallback": False,
            }
        for rule in cfg.rules:
            if rule.default:
                return {
                    "route": rule.name,
                    "routes": [rule.name],
                    "reason": f"LLM selected no valid route from {raw!r}, defaulted",
                    "used_fallback": True,
                }
        raise ValueError(f"LLM selected no valid route from response: {raw!r}")


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
    """Internal helper for the matched summaries step.

    Args:
        trace (GroupTrace): The trace.

    Returns:
        list[str]: The summaries.
    """
    collected: list[str] = []
    for child in trace.children:
        if isinstance(child, GroupTrace):
            if child.matched:
                collected.extend(_matched_summaries(child))
        elif child.matched:
            collected.append(child.summary)
    return collected
