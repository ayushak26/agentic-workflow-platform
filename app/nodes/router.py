"""RouterAgent: rule-based or LLM-judged branching.

The compiler's conditional edge reads `node_outputs[router_id]["route"]`.
This node's job is to write that field correctly."""
from __future__ import annotations

import operator
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry


class RouteRule(BaseModel):
    """One rule. The first matching rule wins. `default` matches anything."""
    name: str
    condition: str | None = None                # e.g. "rfp_intel.parsed.industry == 'finance'"
    default: bool = False


class RouterConfig(BaseModel):
    mode: Literal["rule", "llm"] = "rule"
    rules: list[RouteRule]
    # llm-mode only:
    model: str | None = None
    prompt: str | None = None                   # asks the model to pick a route name
    context: str | None = None                  # context passed to the LLM (templated)


class RouterOutput(BaseModel):
    route: str
    reason: str | None = None


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
    description = "Branch the workflow rule-based or via LLM judgment."
    input_schema = RouterInput
    output_schema = RouterOutput
    config_schema = RouterConfig

    async def run(self, state, resolved_config: dict[str, Any]) -> dict[str, Any]:
        cfg = RouterConfig(**resolved_config)
        if cfg.mode == "rule":
            return self._route_by_rule(cfg, state)
        return await self._route_by_llm(cfg)

    def _route_by_rule(self, cfg, state):
        for rule in cfg.rules:
            if rule.default:
                continue
            if self._eval_condition(rule.condition, state):
                return {"route": rule.name, "reason": f"rule '{rule.condition}' matched"}
        # Fallback to default
        for rule in cfg.rules:
            if rule.default:
                return {"route": rule.name, "reason": "default route"}
        raise ValueError(f"Router {self.node_id} matched no rule and has no default")

    def _eval_condition(self, condition: str | None, state: dict) -> bool:
        """Safe expression eval — supports only `path OP literal`.
        No arbitrary Python. Extend with `and`/`or` later if needed."""
        if not condition:
            return False
        for op_str, op_fn in _SAFE_OPS.items():
            if f" {op_str} " in condition:
                lhs, rhs = condition.split(f" {op_str} ", 1)
                lhs_val = self._resolve_path(lhs.strip(), state)
                rhs_val = self._parse_literal(rhs.strip())
                return op_fn(lhs_val, rhs_val)
        raise ValueError(f"Unparseable condition: {condition!r}")

    def _resolve_path(self, path: str, state: dict) -> Any:
        # path like "rfp_intel.parsed.industry"
        parts = path.split(".")
        node_outputs = state.get("node_outputs", {})
        cursor: Any = node_outputs if parts[0] in node_outputs else state
        for p in parts:
            cursor = cursor[p] if isinstance(cursor, dict) else getattr(cursor, p)
        return cursor

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
                    return {"route": r.name, "reason": f"LLM returned invalid '{choice}', defaulted"}
            raise ValueError(f"LLM returned unknown route: {choice!r}")
        return {"route": choice, "reason": "LLM judgment"}
