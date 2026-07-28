"""Deterministic, zero-token model selection for automatic workflow routing.

The router deliberately does not call an LLM to choose another LLM. It uses
the actual request shape, node role, configured provider availability, cost
catalog, and optional offline evaluation scores. This makes the decision fast,
repeatable, testable, and visible to operators before provider usage starts.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Literal, Mapping, Sequence

from app.llm.model_catalog import (
    AUTO_MODEL,
    MODEL_PROFILE_BY_NAME,
    ModelProfile,
    estimate_model_cost,
)


TaskKind = Literal[
    "classification",
    "coding",
    "extraction",
    "general",
    "reasoning",
    "structured",
    "summarization",
    "tool_use",
    "writing",
]
Complexity = Literal["simple", "moderate", "complex"]
AccuracyPriority = Literal["maximum", "balanced", "economy"]

_TIER_VALUE = {"economy": 1, "standard": 2, "premium": 3}

_TASK_TERMS: dict[TaskKind, tuple[str, ...]] = {
    "classification": (
        "classify",
        "classification",
        "route",
        "label",
        "choose one",
        "yes or no",
    ),
    "coding": (
        "code",
        "python",
        "typescript",
        "sql",
        "debug",
        "function",
        "api",
    ),
    "extraction": (
        "extract",
        "parse",
        "normalize",
        "fields",
        "entities",
        "metadata",
    ),
    "reasoning": (
        "analyse",
        "analyze",
        "evaluate",
        "critique",
        "verify",
        "trade-off",
        "strategy",
        "decision",
        "methodology",
    ),
    "summarization": (
        "summarize",
        "summarise",
        "summary",
        "condense",
    ),
    "writing": (
        "draft",
        "write",
        "rewrite",
        "proposal",
        "narrative",
        "section",
        "concept note",
    ),
    "general": (),
    "structured": (),
    "tool_use": (),
}

_HIGH_STAKES_TERMS = (
    "compliance",
    "contract",
    "financial",
    "grant",
    "horizon europe",
    "legal",
    "medical",
    "policy",
    "safety",
    "scientific evidence",
)


class ModelRoutingError(RuntimeError):
    """Raised when an automatic selection cannot satisfy its constraints."""


@dataclass(frozen=True)
class ModelSelection:
    requested_model: str
    selected_model: str
    mode: Literal["auto", "manual", "cost_protection"]
    task_kind: TaskKind
    complexity: Complexity
    reason: str
    estimated_cost_usd: float
    candidate_models: tuple[str, ...]
    accuracy_priority: AccuracyPriority
    score_source: Literal["policy", "evaluation"]

    def to_event(
        self,
        *,
        actual_model: str,
        call_id: int,
        fallback: bool = False,
        cache_hit: bool = False,
    ) -> dict[str, Any]:
        return {
            "call_id": call_id,
            "requested_model": self.requested_model,
            "selected_model": self.selected_model,
            "actual_model": actual_model,
            "mode": self.mode,
            "task_kind": self.task_kind,
            "complexity": self.complexity,
            "reason": self.reason,
            "estimated_cost_usd": self.estimated_cost_usd,
            "candidate_models": list(self.candidate_models),
            "accuracy_priority": self.accuracy_priority,
            "score_source": self.score_source,
            "fallback": fallback,
            "cache_hit": cache_hit,
        }


class ModelRouter:
    """Choose the most suitable configured model without spending tokens."""

    def select(
        self,
        *,
        method_name: str,
        kwargs: Mapping[str, Any],
        input_tokens: int,
        allowed_models: Sequence[str],
        is_available: Callable[[str], bool],
        node_type: str | None = None,
        policy: Mapping[str, Any] | None = None,
    ) -> ModelSelection:
        policy = dict(policy or {})
        priority = _accuracy_priority(policy.get("accuracy_priority"))
        task = infer_task_kind(
            method_name,
            kwargs,
            node_type=node_type,
        )
        complexity = infer_complexity(
            method_name,
            kwargs,
            input_tokens=input_tokens,
            node_type=node_type,
        )
        requested_output = max(1, int(kwargs.get("max_tokens", 1_024)))

        profiles = [
            MODEL_PROFILE_BY_NAME[model]
            for model in allowed_models
            if model != AUTO_MODEL
            and model in MODEL_PROFILE_BY_NAME
            and is_available(model)
        ]
        if not profiles:
            raise ModelRoutingError(
                "Automatic model selection found no configured provider in "
                "this node's allowed_models list."
            )

        estimated_costs = {
            profile.name: estimate_model_cost(
                profile.name,
                input_tokens,
                requested_output,
            )
            for profile in profiles
        }
        ceiling = policy.get("max_estimated_cost_usd")
        if ceiling is not None:
            ceiling_value = float(ceiling)
            profiles = [
                profile
                for profile in profiles
                if estimated_costs[profile.name] <= ceiling_value
            ]
            if not profiles:
                cheapest = min(estimated_costs, key=estimated_costs.get)
                raise ModelRoutingError(
                    "No configured model satisfies max_estimated_cost_usd="
                    f"{ceiling_value:.6f}; the lowest estimate is "
                    f"{cheapest} at ${estimated_costs[cheapest]:.6f}."
                )

        quality_scores = {
            str(model): float(score)
            for model, score in dict(policy.get("quality_scores") or {}).items()
            if model in MODEL_PROFILE_BY_NAME
        }
        source: Literal["policy", "evaluation"] = (
            "evaluation" if quality_scores else "policy"
        )
        max_cost = max(
            (estimated_costs[profile.name] for profile in profiles),
            default=0.0,
        )
        scored = [
            (
                _score_profile(
                    profile,
                    task=task,
                    complexity=complexity,
                    priority=priority,
                    estimated_cost=estimated_costs[profile.name],
                    max_cost=max_cost,
                    prefer_low_latency=bool(
                        policy.get("prefer_low_latency", False)
                    ),
                    observed_quality=quality_scores.get(profile.name),
                ),
                profile,
            )
            for profile in profiles
        ]
        scored.sort(
            key=lambda item: (
                item[0],
                -estimated_costs[item[1].name],
                item[1].name,
            ),
            reverse=True,
        )
        selected = scored[0][1]
        reason = _selection_reason(
            selected,
            task=task,
            complexity=complexity,
            priority=priority,
            source=source,
            estimated_cost=estimated_costs[selected.name],
            ceiling=float(ceiling) if ceiling is not None else None,
        )
        return ModelSelection(
            requested_model=AUTO_MODEL,
            selected_model=selected.name,
            mode="auto",
            task_kind=task,
            complexity=complexity,
            reason=reason,
            estimated_cost_usd=estimated_costs[selected.name],
            candidate_models=tuple(profile.name for _, profile in scored),
            accuracy_priority=priority,
            score_source=source,
        )

    def describe_manual(
        self,
        *,
        requested_model: str,
        selected_model: str,
        method_name: str,
        kwargs: Mapping[str, Any],
        input_tokens: int,
        node_type: str | None = None,
        mode: Literal["manual", "cost_protection"] = "manual",
    ) -> ModelSelection:
        task = infer_task_kind(method_name, kwargs, node_type=node_type)
        complexity = infer_complexity(
            method_name,
            kwargs,
            input_tokens=input_tokens,
            node_type=node_type,
        )
        estimate = estimate_model_cost(
            selected_model,
            input_tokens,
            int(kwargs.get("max_tokens", 1_024)),
        )
        reason = (
            "Daily cost protection selected the configured emergency model."
            if mode == "cost_protection"
            else "The workflow explicitly selected this model."
        )
        return ModelSelection(
            requested_model=requested_model,
            selected_model=selected_model,
            mode=mode,
            task_kind=task,
            complexity=complexity,
            reason=reason,
            estimated_cost_usd=estimate,
            candidate_models=(selected_model,),
            accuracy_priority="balanced",
            score_source="policy",
        )


def infer_task_kind(
    method_name: str,
    kwargs: Mapping[str, Any],
    *,
    node_type: str | None = None,
) -> TaskKind:
    if method_name == "chat_with_tools" or kwargs.get("tools"):
        return "tool_use"
    if method_name == "complete_structured" or kwargs.get("response_model"):
        return "structured"

    payload = _request_text(kwargs, node_type=node_type)
    scores = {
        task: sum(payload.count(term) for term in terms)
        for task, terms in _TASK_TERMS.items()
        if terms
    }
    if scores and max(scores.values()) > 0:
        if (
            any(term in payload for term in ("draft", "write", "rewrite"))
            and scores.get("writing", 0) >= max(scores.values())
        ):
            return "writing"
        return max(scores, key=scores.get)  # type: ignore[return-value]
    return "general"


def infer_complexity(
    method_name: str,
    kwargs: Mapping[str, Any],
    *,
    input_tokens: int,
    node_type: str | None = None,
) -> Complexity:
    points = 0
    if input_tokens >= 12_000:
        points += 2
    elif input_tokens >= 4_000:
        points += 1

    output_tokens = int(kwargs.get("max_tokens", 1_024))
    if output_tokens >= 6_000:
        points += 2
    elif output_tokens >= 2_500:
        points += 1

    if method_name == "complete_structured" or kwargs.get("response_model"):
        points += 1
    tools = kwargs.get("tools") or []
    if tools:
        points += 2 if len(tools) >= 4 else 1

    payload = _request_text(kwargs, node_type=node_type)
    if any(term in payload for term in _HIGH_STAKES_TERMS):
        points += 2
    if any(
        term in (node_type or "").lower()
        for term in ("evaluation", "verifier", "concept", "evidence")
    ):
        points += 1

    if points <= 1:
        return "simple"
    if points <= 3:
        return "moderate"
    return "complex"


def _request_text(
    kwargs: Mapping[str, Any],
    *,
    node_type: str | None,
) -> str:
    try:
        serialized = json.dumps(
            {
                "system": kwargs.get("system", ""),
                "user": kwargs.get("user", ""),
                "messages": kwargs.get("messages", []),
                "node_type": node_type or "",
            },
            default=str,
        )
    except Exception:
        serialized = " ".join(
            (
                str(kwargs.get("system", "")),
                str(kwargs.get("user", "")),
                str(kwargs.get("messages", "")),
                node_type or "",
            )
        )
    return serialized.lower()


def _accuracy_priority(value: Any) -> AccuracyPriority:
    return (
        value
        if value in {"maximum", "balanced", "economy"}
        else "balanced"
    )


def _target_tier(
    complexity: Complexity,
    priority: AccuracyPriority,
) -> int:
    base = {"simple": 1, "moderate": 2, "complex": 3}[complexity]
    if priority == "maximum":
        return min(3, base + 1)
    if priority == "economy":
        return max(1, base - 1)
    return base


def _score_profile(
    profile: ModelProfile,
    *,
    task: TaskKind,
    complexity: Complexity,
    priority: AccuracyPriority,
    estimated_cost: float,
    max_cost: float,
    prefer_low_latency: bool,
    observed_quality: float | None,
) -> float:
    tier = _TIER_VALUE[profile.tier]
    if observed_quality is not None:
        base = max(0.0, min(1.0, observed_quality)) * 100
    else:
        base = {1: 62.0, 2: 78.0, 3: 92.0}[tier]
        if task in profile.strengths:
            base += 8.0
        elif "general" in profile.strengths:
            base += 3.0

        target = _target_tier(complexity, priority)
        if tier < target:
            base -= 32.0 * (target - tier)
        elif tier > target:
            overkill = {
                "maximum": 4.0,
                "balanced": 12.0,
                "economy": 20.0,
            }[priority]
            base -= overkill * (tier - target)

    normalized_cost = estimated_cost / max_cost if max_cost else 0.0
    cost_weight = {
        "maximum": 2.0,
        "balanced": 8.0,
        "economy": 18.0,
    }[priority]
    base -= cost_weight * normalized_cost
    if prefer_low_latency:
        base += profile.speed_rank * 3.0
    else:
        base += profile.speed_rank
    return base


def _selection_reason(
    profile: ModelProfile,
    *,
    task: TaskKind,
    complexity: Complexity,
    priority: AccuracyPriority,
    source: Literal["policy", "evaluation"],
    estimated_cost: float,
    ceiling: float | None,
) -> str:
    evidence = (
        "node-specific evaluation scores"
        if source == "evaluation"
        else "the configured capability and cost policy"
    )
    strength = (
        f" and a {task.replace('_', ' ')} strength"
        if task in profile.strengths
        else ""
    )
    budget = (
        f", within the ${ceiling:.6f} call ceiling"
        if ceiling is not None
        else ""
    )
    return (
        f"Selected for a {complexity} {task.replace('_', ' ')} request using "
        f"{evidence}; {profile.tier} tier{strength}, "
        f"{priority} accuracy priority, estimated maximum "
        f"${estimated_cost:.6f}{budget}."
    )
