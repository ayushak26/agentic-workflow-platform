#!/usr/bin/env python3
"""Live health check for every LLM in OpenRouter's catalog.

Enumerates OpenRouter's own `GET /v1/models` (public, no key needed — see
app/llm/openrouter_catalog.py) and probes each text-output model through the real
production gateway, `app.llm.openrouter_gw.OpenRouterGateway`. Using the gateway
rather than raw httpx is the point: a green run means Eurskem's own dispatch path
works for that model, not merely that OpenRouter is up.

Three probes per model, each mapped to one gateway method a workflow node actually
uses:

    text        complete()             — TransformAgent / plain generation
    structured  complete_structured()  — every node with a Pydantic output schema
    tools       chat_with_tools()      — McpToolAgent, RouterAgent, agentic loops

A probe is *skipped* when OpenRouter's catalog says the model does not support that
capability; it is a *failure* when the catalog claims support and the call does not
deliver. That distinction is the whole value of the report — "claims tools, cannot
tool-call" is the failure mode that silently breaks workflows at 3am, and the
catalog's `supported_parameters` is the only thing the Builder UI has to go on.

Verdicts:

    healthy   text passed, every claimed capability passed
    degraded  text passed, but a capability the catalog claims is broken
    down      text failed — the model cannot be used at all

Usage:

    python scripts/openrouter_health_check.py --dry-run          # plan + cost ceiling
    python scripts/openrouter_health_check.py --filter anthropic # one vendor
    python scripts/openrouter_health_check.py --json report.json # full sweep

Cost is real but small: probes are capped at a few dozen output tokens each. Run
--dry-run first — it prints the worst-case spend before a single billable call.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.config import settings
from app.llm.openrouter_gw import OpenRouterGateway

# Output-token ceilings per probe. Deliberately small — this measures liveness, not
# quality. The catch: reasoning models bill hidden thinking as output tokens and emit
# it *before* any visible content, so a tight ceiling truncates a perfectly healthy
# model into a 200-with-empty-content and the check reports a false outage. Measured
# live: google/gemini-3.7-flash spent 29 of 32 tokens thinking and returned "". Two
# thirds of OpenRouter's catalog advertises `reasoning`, so the reasoning ceilings are
# the ones that actually govern this sweep.
TEXT_MAX_TOKENS = 32
REASONING_TEXT_MAX_TOKENS = 1024
STRUCTURED_MAX_TOKENS = 512
REASONING_STRUCTURED_MAX_TOKENS = 2048
TOOLS_MAX_TOKENS = 512
REASONING_TOOLS_MAX_TOKENS = 2048

# Rough prompt size for the worst-case cost estimate. Real prompts run smaller.
ESTIMATED_INPUT_TOKENS = 220

PROBE_NAMES = ("text", "structured", "tools")

# This account's OpenRouter allowed-providers setting. A model with no endpoint on one
# of these is unreachable no matter how healthy it is upstream — OpenRouter rejects the
# call with "No allowed providers are available for the selected model" — so probing it
# would bill nothing but would fill the report with failures that say nothing about
# model health. Values are OpenRouter provider *slugs* (GET /v1/providers); display
# names drift, slugs do not.
#
# Two mappings worth stating outright, since neither is guessable from the console UI:
#   "SpaceXAI"          -> "xai"               OpenRouter lists exactly one xAI provider.
#   "Google AI Studio"  -> "google-ai-studio"  NOT "google-vertex", which OpenRouter
#                                              calls plain "Google". A model served only
#                                              on Vertex is correctly excluded here.
# "Alibaba" (slug "alibaba") is deliberately absent — removed from the allowlist.
DEFAULT_ALLOWED_PROVIDERS = (
    "xai",
    "z-ai",
    "nvidia",
    "openai",
    "deepseek",
    "anthropic",
    "moonshotai",
    "google-ai-studio",
)

_HEALTH_TOOL = {
    "name": "report_status",
    "description": "Report the health status of this model.",
    "input_schema": {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "description": "The literal string 'ok'.",
            }
        },
        "required": ["status"],
        "additionalProperties": False,
    },
}


class HealthReport(BaseModel):
    """Structured-output probe target. Two scalars of different types, because a
    model that stringifies integers passes a one-string schema and still breaks
    every real node."""

    status: str = Field(description="The literal string 'ok'.")
    number: int = Field(description="The integer 7.")


# --------------------------------------------------------------------------- catalog


@dataclass(frozen=True)
class CatalogEntry:
    """One OpenRouter catalog row, narrowed to what this check needs.

    Not `OpenRouterModelInfo` (app/llm/openrouter_catalog.py): that type is shaped for
    the Builder's model picker and drops `output_modalities` and the raw
    `supported_parameters` set, both of which this script filters and skips on.
    """

    id: str  # bare OpenRouter id, e.g. "openai/gpt-4o-mini"
    name: str
    output_modalities: tuple[str, ...]
    supported_parameters: frozenset[str]
    input_usd_per_million: float
    output_usd_per_million: float
    # OpenRouter's own routers (auto, fusion, pareto-code, bodybuilder) price at "-1":
    # the real cost depends on which upstream model the router picks, so it cannot be
    # known ahead of the call. Clamped to 0.0 above and flagged here rather than left
    # negative, which would silently subtract from the pre-flight cost ceiling.
    variable_pricing: bool = False

    @property
    def platform_id(self) -> str:
        """Eurskem's gateway-routed form — the id a workflow YAML would name.

        Double-prefixed for the routers ("openrouter/openrouter/auto") and that is
        correct: the gateway strips exactly one prefix, leaving the "openrouter/auto"
        that OpenRouter's API expects.
        """
        return f"openrouter/{self.id}"

    @property
    def emits_text(self) -> bool:
        """The emits text."""
        return "text" in self.output_modalities

    @property
    def is_text_only(self) -> bool:
        """Text and nothing else. Models that also emit image or audio (Lyria,
        gpt-audio, gpt-5-image) list "text" among their output modalities but are not
        chat LLMs — they reject a plain completion with HTTP 400 and would otherwise be
        reported as outages when nothing is actually wrong with them."""
        return set(self.output_modalities) == {"text"}

    @property
    def is_batch_endpoint(self) -> bool:
        """`:batch` variants route to OpenRouter's asynchronous Batch API, which the
        synchronous /chat/completions path this gateway uses does not exercise."""
        return self.id.endswith(":batch")

    @property
    def claims_structured(self) -> bool:
        """The claims structured."""
        return bool(
            {"structured_outputs", "response_format"} & self.supported_parameters
        )

    @property
    def claims_tools(self) -> bool:
        """The claims tools."""
        return "tools" in self.supported_parameters

    @property
    def claims_reasoning(self) -> bool:
        """The claims reasoning."""
        return bool(
            {"reasoning", "include_reasoning", "reasoning_effort"}
            & self.supported_parameters
        )

    @property
    def ceilings(self) -> dict[str, int]:
        """Per-probe output-token budgets, widened for models that think out loud."""
        if self.claims_reasoning:
            return {
                "text": REASONING_TEXT_MAX_TOKENS,
                "structured": REASONING_STRUCTURED_MAX_TOKENS,
                "tools": REASONING_TOOLS_MAX_TOKENS,
            }
        return {
            "text": TEXT_MAX_TOKENS,
            "structured": STRUCTURED_MAX_TOKENS,
            "tools": TOOLS_MAX_TOKENS,
        }

    def worst_case_cost_usd(self, probes: Iterable[str]) -> float:
        """Compute the worst case cost usd.

        Args:
            probes (Iterable[str]): The probes.

        Returns:
            float: The case cost usd.
        """
        ceilings = self.ceilings
        total = 0.0
        for probe in probes:
            if probe == "structured" and not self.claims_structured:
                continue
            if probe == "tools" and not self.claims_tools:
                continue
            total += (
                ESTIMATED_INPUT_TOKENS * self.input_usd_per_million
                + ceilings[probe] * self.output_usd_per_million
            ) / 1_000_000
        return total


def _price(raw: Any) -> float | None:
    """OpenRouter prices are per-token USD strings; report per-million. None means the
    price is not knowable up front (missing, unparseable, or the routers' "-1")."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value * 1_000_000 if value >= 0 else None


async def fetch_catalog() -> list[CatalogEntry]:
    """Fetch the catalog.

    Returns:
        list[CatalogEntry]: The catalog.
    """
    async with httpx.AsyncClient(
        base_url=settings.openrouter_base_url,
        timeout=settings.openrouter_request_timeout_seconds,
    ) as client:
        response = await client.get("/models")
        response.raise_for_status()
        body = response.json()

    entries: list[CatalogEntry] = []
    for row in body.get("data") or []:
        model_id = row.get("id")
        if not isinstance(model_id, str) or not model_id:
            continue
        architecture = row.get("architecture") or {}
        pricing = row.get("pricing") or {}
        input_price = _price(pricing.get("prompt"))
        output_price = _price(pricing.get("completion"))
        entries.append(
            CatalogEntry(
                id=model_id,
                name=row.get("name") or model_id,
                output_modalities=tuple(architecture.get("output_modalities") or ()),
                supported_parameters=frozenset(row.get("supported_parameters") or ()),
                input_usd_per_million=input_price or 0.0,
                output_usd_per_million=output_price or 0.0,
                variable_pricing=input_price is None or output_price is None,
            )
        )
    return sorted(entries, key=lambda entry: entry.id)


async def fetch_provider_slugs(client: httpx.AsyncClient) -> dict[str, str]:
    """Live provider registry as {lowercased name or slug: slug}, so an allowlist can be
    given in either form and a typo fails loudly instead of silently matching nothing."""
    response = await client.get("/providers")
    response.raise_for_status()
    lookup: dict[str, str] = {}
    for row in (response.json().get("data") or []):
        slug = row.get("slug")
        if not isinstance(slug, str) or not slug:
            continue
        lookup[slug.lower()] = slug
        name = row.get("name")
        if isinstance(name, str) and name:
            lookup[name.lower()] = slug
    return lookup


def resolve_providers(
    requested: Iterable[str], lookup: dict[str, str]
) -> tuple[set[str], list[str]]:
    """Map user-facing provider names onto slugs. Returns (resolved, unknown)."""
    resolved: set[str] = set()
    unknown: list[str] = []
    for raw in requested:
        key = raw.strip().lower()
        if not key:
            continue
        slug = lookup.get(key)
        if slug is None:
            unknown.append(raw)
        else:
            resolved.add(slug)
    return resolved, unknown


async def fetch_model_providers(
    client: httpx.AsyncClient, model_id: str, semaphore: asyncio.Semaphore
) -> tuple[str, set[str]]:
    """Which providers actually serve this model, via GET /models/{id}/endpoints.

    The catalog listing does not carry this, and a model's author is not its provider —
    NVIDIA serves plenty of models it did not train, and several `google/*` models are
    Vertex-only. Unauthenticated and free; only the probes below cost anything. A model
    whose endpoints cannot be read is returned with an empty set, which the caller
    reports as excluded-with-reason rather than silently dropping.
    """
    async with semaphore:
        try:
            response = await client.get(f"/models/{model_id}/endpoints")
            response.raise_for_status()
            data = response.json().get("data") or {}
        except (httpx.HTTPError, ValueError):
            return model_id, set()
    providers = {
        endpoint.get("provider_name")
        for endpoint in (data.get("endpoints") or [])
        if isinstance(endpoint.get("provider_name"), str)
    }
    return model_id, {p for p in providers if p}


# ---------------------------------------------------------------------------- probes


class FatalProviderError(RuntimeError):
    """Account-level failure (bad key, no credits). Every remaining model would fail
    the same way, so the sweep aborts instead of burning minutes proving it."""


@dataclass
class ProbeResult:
    """Provides the ProbeResult behaviour.

    Attributes:
        status (str).
        detail (str).
        seconds (float).
        cost_usd (float | None).
        output_tokens (int).
    """
    status: str  # "ok" | "fail" | "skip"
    detail: str = ""
    seconds: float = 0.0
    cost_usd: float | None = None
    output_tokens: int = 0

    def as_dict(self) -> dict[str, Any]:
        """Compute the as dict.

        Returns:
            dict[str, Any]: The dict.
        """
        return {
            "status": self.status,
            "detail": self.detail,
            "seconds": round(self.seconds, 2),
            "cost_usd": self.cost_usd,
            "output_tokens": self.output_tokens,
        }


@dataclass
class ModelResult:
    """Provides the ModelResult behaviour.

    Attributes:
        model (str).
        display_name (str).
        verdict (str).
        probes (dict[str, ProbeResult]).
        seconds (float).
    """
    model: str
    display_name: str
    verdict: str = "down"
    probes: dict[str, ProbeResult] = field(default_factory=dict)
    seconds: float = 0.0

    @property
    def cost_usd(self) -> float:
        """The cost usd."""
        return sum(p.cost_usd or 0.0 for p in self.probes.values())

    def as_dict(self) -> dict[str, Any]:
        """Compute the as dict.

        Returns:
            dict[str, Any]: The dict.
        """
        return {
            "model": self.model,
            "display_name": self.display_name,
            "verdict": self.verdict,
            "seconds": round(self.seconds, 2),
            "cost_usd": round(self.cost_usd, 6),
            "probes": {name: probe.as_dict() for name, probe in self.probes.items()},
        }


def _http_detail(error: httpx.HTTPStatusError) -> str:
    """OpenRouter puts the useful part in `error.message`; the raw body is noisy."""
    code = error.response.status_code
    message = ""
    try:
        payload = error.response.json()
        if isinstance(payload, dict):
            err = payload.get("error")
            if isinstance(err, dict):
                message = str(err.get("message") or "")
            elif isinstance(err, str):
                message = err
    except ValueError:
        message = error.response.text[:200]
    message = " ".join(message.split())[:180]
    return f"HTTP {code}{': ' + message if message else ''}"


def _classify(error: BaseException) -> str:
    """Classify the result.

    Args:
        error (BaseException): Error value or message.

    Returns:
        str: The result.
    """
    if isinstance(error, httpx.HTTPStatusError):
        return _http_detail(error)
    if isinstance(error, asyncio.TimeoutError):
        return "timeout"
    if isinstance(error, ValidationError):
        return f"schema mismatch: {' '.join(str(error).split())[:160]}"
    if isinstance(error, httpx.HTTPError):
        return f"{type(error).__name__}: {error}"[:180]
    return f"{type(error).__name__}: {error}"[:180]


def _is_fatal(error: BaseException) -> bool:
    """Return whether fatal.

    Args:
        error (BaseException): Error value or message.

    Returns:
        bool: True when fatal.
    """
    return (
        isinstance(error, httpx.HTTPStatusError)
        and error.response.status_code in (401, 402, 403)
    )


def _is_retryable(error: BaseException) -> bool:
    """Return whether retryable.

    Args:
        error (BaseException): Error value or message.

    Returns:
        bool: True when retryable.
    """
    if isinstance(error, asyncio.TimeoutError):
        return False
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code == 429 or error.response.status_code >= 500
    return isinstance(error, (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError))


async def _run_probe(
    call: Any,
    *,
    timeout: float,
    retry_delay: float,
) -> tuple[ProbeResult, Any]:
    """Run one gateway call under a wall-clock cap, with a single retry on transient
    failures (429 / 5xx / dropped connection).

    Returns `(probe, raw_response)`. A transport-level success is only a provisional
    "ok" — the caller still has to check what came back, since an empty completion or
    a missing tool call arrives as a perfectly valid HTTP 200.
    """
    started = time.monotonic()
    for attempt in (1, 2):
        try:
            result = await asyncio.wait_for(call(), timeout=timeout)
            return ProbeResult(
                status="ok",
                seconds=time.monotonic() - started,
                cost_usd=getattr(result, "cost_usd", None),
                output_tokens=getattr(result, "output_tokens", 0),
                detail="",
            ), result
        except Exception as error:  # noqa: BLE001 — every failure is a data point
            if _is_fatal(error):
                raise FatalProviderError(_http_detail(error)) from error
            if attempt == 1 and _is_retryable(error):
                await asyncio.sleep(retry_delay)
                continue
            return ProbeResult(
                status="fail",
                detail=_classify(error),
                seconds=time.monotonic() - started,
            ), None
    raise AssertionError("unreachable")


async def probe_model(
    gateway: OpenRouterGateway,
    entry: CatalogEntry,
    *,
    probes: tuple[str, ...],
    timeout: float,
    retry_delay: float,
) -> ModelResult:
    """Probe the model.

    Args:
        gateway (OpenRouterGateway): LLM gateway.
        entry (CatalogEntry): Ledger entry.
        probes (tuple[str, ...]): The probes.
        timeout (float): Timeout in seconds.
        retry_delay (float): The retry delay.

    Returns:
        ModelResult: The model.
    """
    result = ModelResult(model=entry.platform_id, display_name=entry.name)
    started = time.monotonic()
    model = entry.platform_id
    ceilings = entry.ceilings

    if "text" in probes:
        probe, response = await _run_probe(
            lambda: gateway.complete(
                model=model,
                system="You are a health-check endpoint. Answer in one word.",
                user="Reply with exactly: OK",
                temperature=0.0,
                max_tokens=ceilings["text"],
            ),
            timeout=timeout,
            retry_delay=retry_delay,
        )
        if probe.status == "ok" and not (response.text or "").strip():
            # A 200 with empty content is a failure a naive check would score as a pass.
            # Distinguish the two ways it happens: the model produced nothing at all, or
            # it spent the whole budget on hidden reasoning and got cut off. The second
            # says the ceiling is too low for this model, not that the model is down.
            starved = probe.output_tokens >= ceilings["text"]
            probe = ProbeResult(
                status="fail",
                detail=(
                    f"no visible text — {probe.output_tokens} output tokens consumed by "
                    f"hidden reasoning before the {ceilings['text']}-token ceiling"
                    if starved
                    else "empty completion"
                ),
                seconds=probe.seconds,
                cost_usd=probe.cost_usd,
                output_tokens=probe.output_tokens,
            )
        result.probes["text"] = probe

    if "structured" in probes:
        if not entry.claims_structured:
            result.probes["structured"] = ProbeResult(
                status="skip", detail="not advertised by catalog"
            )
        else:
            probe, response = await _run_probe(
                lambda: gateway.complete_structured(
                    model=model,
                    system="You are a health-check endpoint.",
                    user='Report status "ok" and number 7.',
                    response_model=HealthReport,
                    temperature=0.0,
                    max_tokens=ceilings["structured"],
                ),
                timeout=timeout,
                retry_delay=retry_delay,
            )
            if probe.status == "ok" and response.parsed.number != 7:
                # Schema held but the instruction did not land. Not a gateway defect —
                # recorded as a pass with a note so it does not mask real breakage.
                probe.detail = f"schema ok, instruction drift (number={response.parsed.number})"
            result.probes["structured"] = probe

    if "tools" in probes:
        if not entry.claims_tools:
            result.probes["tools"] = ProbeResult(
                status="skip", detail="not advertised by catalog"
            )
        else:
            probe, response = await _run_probe(
                lambda: gateway.chat_with_tools(
                    model=model,
                    system="You are a health-check endpoint. Use the tool provided.",
                    messages=[
                        {
                            "role": "user",
                            "content": "Call report_status with status 'ok'.",
                        }
                    ],
                    tools=[_HEALTH_TOOL],
                    temperature=0.0,
                    max_tokens=TOOLS_MAX_TOKENS,
                ),
                timeout=timeout,
                retry_delay=retry_delay,
            )
            if probe.status == "ok" and not response.tool_calls:
                probe = ProbeResult(
                    status="fail",
                    detail="no tool call emitted",
                    seconds=probe.seconds,
                    cost_usd=probe.cost_usd,
                    output_tokens=probe.output_tokens,
                )
            elif probe.status == "ok" and response.tool_calls[0].name != "report_status":
                probe = ProbeResult(
                    status="fail",
                    detail=f"called unknown tool {response.tool_calls[0].name!r}",
                    seconds=probe.seconds,
                    cost_usd=probe.cost_usd,
                    output_tokens=probe.output_tokens,
                )
            result.probes["tools"] = probe

    result.seconds = time.monotonic() - started
    text_probe = result.probes.get("text")
    failed = [name for name, p in result.probes.items() if p.status == "fail"]
    if text_probe is not None and text_probe.status == "fail":
        result.verdict = "down"
    elif failed:
        result.verdict = "degraded"
    else:
        result.verdict = "healthy"
    return result


# ------------------------------------------------------------------------------- run


def _select(
    catalog: list[CatalogEntry], args: argparse.Namespace
) -> tuple[list[CatalogEntry], list[tuple[str, str]]]:
    """Returns (selected, excluded) — excluded rows are reported, never silently dropped."""
    selected: list[CatalogEntry] = []
    excluded: list[tuple[str, str]] = []

    # Accept either form of an id. Both the bare catalog id and the platform id are kept
    # as candidates rather than blindly stripping the prefix: OpenRouter's own routers
    # are genuinely *named* "openrouter/auto", so stripping would turn a valid id into
    # the nonexistent "auto".
    explicit: set[str] = set()
    for raw in args.models or []:
        value = raw.strip()
        if not value:
            continue
        explicit.add(value)
        if value.startswith("openrouter/"):
            explicit.add(value[len("openrouter/"):])

    for entry in catalog:
        if explicit:
            if entry.id not in explicit:
                continue
        elif args.filter and args.filter.lower() not in entry.id.lower():
            continue
        if not entry.emits_text:
            excluded.append((entry.id, f"non-text output ({'+'.join(entry.output_modalities) or 'none'})"))
            continue
        if not entry.is_text_only and not args.include_multimodal_output:
            excluded.append(
                (
                    entry.id,
                    f"also emits {'+'.join(m for m in entry.output_modalities if m != 'text')}"
                    " — not a chat LLM (--include-multimodal-output to probe)",
                )
            )
            continue
        if entry.is_batch_endpoint and not args.include_batch:
            excluded.append((entry.id, "asynchronous Batch API endpoint (--include-batch to probe)"))
            continue
        # A model whose price is unknowable up front cannot be shown to satisfy a spend
        # constraint, so both spend filters exclude it rather than assume it is free.
        if (args.free_only or args.max_output_price is not None) and entry.variable_pricing:
            excluded.append((entry.id, "variable pricing — cannot verify against a spend limit"))
            continue
        if args.free_only and entry.output_usd_per_million > 0:
            excluded.append((entry.id, "not free"))
            continue
        if (
            args.max_output_price is not None
            and entry.output_usd_per_million > args.max_output_price
        ):
            excluded.append(
                (entry.id, f"output ${entry.output_usd_per_million:.2f}/M over ceiling")
            )
            continue
        selected.append(entry)

    if explicit:
        known = {entry.id for entry in catalog}
        for raw in sorted({m.strip() for m in args.models or [] if m.strip()}):
            bare = raw[len("openrouter/"):] if raw.startswith("openrouter/") else raw
            if raw not in known and bare not in known:
                excluded.append((raw, "not in OpenRouter catalog"))

    return selected, excluded


def _apply_limit(
    selected: list[CatalogEntry], excluded: list[tuple[str, str]], limit: int | None
) -> list[CatalogEntry]:
    """Apply the limit.

    Args:
        selected (list[CatalogEntry]): The selected.
        excluded (list[tuple[str, str]]): The excluded.
        limit (int | None): Maximum number of items to return.

    Returns:
        list[CatalogEntry]: The limit.
    """
    if not limit:
        return selected
    excluded.extend((entry.id, f"beyond --limit {limit}") for entry in selected[limit:])
    return selected[:limit]


async def filter_by_provider(
    selected: list[CatalogEntry],
    excluded: list[tuple[str, str]],
    *,
    allowed_slugs: set[str],
    provider_lookup: dict[str, str],
    client: httpx.AsyncClient,
    concurrency: int,
) -> list[CatalogEntry]:
    """Keep only models with at least one endpoint on an allowed provider."""
    semaphore = asyncio.Semaphore(concurrency)
    routers = [entry for entry in selected if entry.id.startswith("openrouter/")]
    resolvable = [entry for entry in selected if not entry.id.startswith("openrouter/")]

    pairs = await asyncio.gather(
        *(fetch_model_providers(client, entry.id, semaphore) for entry in resolvable)
    )
    providers_by_model = dict(pairs)

    # OpenRouter's own routers have no /endpoints of their own — they dispatch to other
    # models and already honour the account's provider restrictions, so they stay in.
    kept: list[CatalogEntry] = list(routers)
    for entry in resolvable:
        names = providers_by_model.get(entry.id) or set()
        if not names:
            excluded.append((entry.id, "no endpoints listed by OpenRouter"))
            continue
        slugs = {provider_lookup.get(name.lower(), name.lower()) for name in names}
        if slugs & allowed_slugs:
            kept.append(entry)
        else:
            excluded.append(
                (entry.id, f"no allowed provider (served by: {', '.join(sorted(names))})")
            )
    return sorted(kept, key=lambda entry: entry.id)


def _verdict_mark(verdict: str) -> str:
    """Internal helper for the verdict mark step.

    Args:
        verdict (str): The verdict.

    Returns:
        str: The mark.
    """
    return {"healthy": "PASS", "degraded": "WARN", "down": "FAIL"}.get(verdict, "????")


def _probe_summary(result: ModelResult) -> str:
    """Probe the summary.

    Args:
        result (ModelResult): Result mapping.

    Returns:
        str: The summary.
    """
    parts = []
    for name in PROBE_NAMES:
        probe = result.probes.get(name)
        if probe is None:
            continue
        mark = {"ok": "ok", "fail": "FAIL", "skip": "-"}[probe.status]
        parts.append(f"{name}={mark}")
    return " ".join(parts)


async def sweep(entries: list[CatalogEntry], args: argparse.Namespace) -> list[ModelResult]:
    """Sweep the result.

    Args:
        entries (list[CatalogEntry]): Entries to process.
        args (argparse.Namespace): Positional arguments.

    Returns:
        list[ModelResult]: The result.
    """
    gateway = OpenRouterGateway(api_key=settings.openrouter_api_key)
    semaphore = asyncio.Semaphore(args.concurrency)
    results: list[ModelResult] = []
    aborted: list[str] = []
    done = 0
    total = len(entries)

    async def worker(entry: CatalogEntry) -> None:
        """Compute the worker.

        Args:
            entry (CatalogEntry): Ledger entry.
        """
        nonlocal done
        if aborted:
            return
        async with semaphore:
            if aborted:
                return
            try:
                result = await probe_model(
                    gateway,
                    entry,
                    probes=tuple(args.probes),
                    timeout=args.timeout,
                    retry_delay=args.retry_delay,
                )
            except FatalProviderError as error:
                aborted.append(str(error))
                return
        results.append(result)
        done += 1
        if not args.quiet:
            print(
                f"[{done:>3}/{total}] {_verdict_mark(result.verdict):<4} "
                f"{result.model:<58} {_probe_summary(result):<34} "
                f"{result.seconds:>5.1f}s  ${result.cost_usd:.6f}",
                flush=True,
            )

    try:
        await asyncio.gather(*(worker(entry) for entry in entries))
    finally:
        await gateway._client.aclose()  # noqa: SLF001 — no public close on the gateway

    if aborted:
        raise FatalProviderError(aborted[0])
    return sorted(results, key=lambda r: r.model)


def report(
    results: list[ModelResult],
    excluded: list[tuple[str, str]],
    args: argparse.Namespace,
) -> int:
    """Report the result.

    Args:
        results (list[ModelResult]): The results.
        excluded (list[tuple[str, str]]): The excluded.
        args (argparse.Namespace): Positional arguments.

    Returns:
        int: The result.
    """
    healthy = [r for r in results if r.verdict == "healthy"]
    degraded = [r for r in results if r.verdict == "degraded"]
    down = [r for r in results if r.verdict == "down"]
    total_cost = sum(r.cost_usd for r in results)

    print()
    print("=" * 100)
    print(f"OpenRouter LLM health check — {len(results)} models probed, ${total_cost:.4f} spent")
    print("=" * 100)
    print(f"  healthy   {len(healthy):>4}   every probed capability works")
    print(f"  degraded  {len(degraded):>4}   text works, an advertised capability does not")
    print(f"  down      {len(down):>4}   no usable completion")
    if excluded:
        print(f"  excluded  {len(excluded):>4}   not probed (see --json report for reasons)")

    for label, group in (("DOWN", down), ("DEGRADED", degraded)):
        if not group:
            continue
        print(f"\n{label}")
        print("-" * 100)
        for result in group:
            broken = [
                f"{name}: {probe.detail}"
                for name, probe in result.probes.items()
                if probe.status == "fail"
            ]
            print(f"  {result.model}")
            for line in broken:
                print(f"      {line}")

    if args.json:
        path = Path(args.json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "probes": list(args.probes),
                    "totals": {
                        "probed": len(results),
                        "healthy": len(healthy),
                        "degraded": len(degraded),
                        "down": len(down),
                        "excluded": len(excluded),
                        "cost_usd": round(total_cost, 6),
                    },
                    "models": [r.as_dict() for r in results],
                    "excluded": [
                        {"model": model_id, "reason": reason}
                        for model_id, reason in excluded
                    ],
                },
                indent=2,
            )
            + "\n"
        )
        print(f"\nFull report written to {path}")

    if down:
        return 1
    if degraded and args.strict:
        return 1
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the args.

    Args:
        argv (list[str] | None): The argv (optional, default None).

    Returns:
        argparse.Namespace: The args.
    """
    parser = argparse.ArgumentParser(
        description="Probe every OpenRouter LLM through Eurskem's production gateway.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--filter", help="Only probe models whose id contains this substring.")
    parser.add_argument(
        "--models",
        nargs="*",
        help="Explicit model ids (with or without the 'openrouter/' prefix). Overrides --filter.",
    )
    parser.add_argument("--limit", type=int, help="Probe at most N models.")
    parser.add_argument(
        "--providers",
        nargs="+",
        default=list(DEFAULT_ALLOWED_PROVIDERS),
        metavar="PROVIDER",
        help=(
            "Provider names or slugs this account is allowed to route to "
            f"(default: {', '.join(DEFAULT_ALLOWED_PROVIDERS)})."
        ),
    )
    parser.add_argument(
        "--all-providers",
        action="store_true",
        help="Skip the provider allowlist and probe the whole catalog.",
    )
    parser.add_argument(
        "--probes",
        nargs="+",
        choices=PROBE_NAMES,
        default=list(PROBE_NAMES),
        help="Which capabilities to probe (default: all three).",
    )
    parser.add_argument("--concurrency", type=int, default=12, help="Models probed in parallel.")
    parser.add_argument("--timeout", type=float, default=90.0, help="Per-call wall-clock cap (s).")
    parser.add_argument("--retry-delay", type=float, default=5.0, help="Backoff before the single retry (s).")
    parser.add_argument("--free-only", action="store_true", help="Only probe zero-cost models.")
    parser.add_argument(
        "--include-batch",
        action="store_true",
        help="Also probe ':batch' variants (asynchronous Batch API; excluded by default).",
    )
    parser.add_argument(
        "--include-multimodal-output",
        action="store_true",
        help="Also probe image/audio-emitting models (excluded by default; not chat LLMs).",
    )
    parser.add_argument(
        "--max-output-price",
        type=float,
        help="Skip models whose completion price exceeds this USD/million ceiling.",
    )
    parser.add_argument("--json", help="Write the full machine-readable report here.")
    parser.add_argument("--quiet", action="store_true", help="Suppress the per-model progress line.")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero on degraded models too.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the plan and worst-case cost without making a billable call.",
    )
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> int:
    """Run the result.

    Args:
        args (argparse.Namespace): Positional arguments.

    Returns:
        int: The result.
    """
    async with httpx.AsyncClient(
        base_url=settings.openrouter_base_url,
        timeout=settings.openrouter_request_timeout_seconds,
    ) as client:
        catalog = await fetch_catalog()
        if not catalog:
            print("OpenRouter returned an empty catalog.", file=sys.stderr)
            return 2

        selected, excluded = _select(catalog, args)

        if not args.all_providers and selected:
            provider_lookup = await fetch_provider_slugs(client)
            allowed_slugs, unknown = resolve_providers(args.providers, provider_lookup)
            if unknown:
                print(
                    "Unknown provider(s): "
                    + ", ".join(repr(name) for name in unknown)
                    + "\nUse a name or slug from GET /v1/providers.",
                    file=sys.stderr,
                )
                return 2
            print(
                f"Restricting to {len(allowed_slugs)} allowed provider(s): "
                f"{', '.join(sorted(allowed_slugs))}"
            )
            print(f"Resolving endpoints for {len(selected)} models...")
            selected = await filter_by_provider(
                selected,
                excluded,
                allowed_slugs=allowed_slugs,
                provider_lookup=provider_lookup,
                client=client,
                concurrency=max(args.concurrency, 16),
            )

    selected = _apply_limit(selected, excluded, args.limit)
    if not selected:
        print("No models matched the selection.", file=sys.stderr)
        for model_id, reason in excluded[:20]:
            print(f"  excluded {model_id}: {reason}", file=sys.stderr)
        return 2

    ceiling = sum(entry.worst_case_cost_usd(args.probes) for entry in selected)
    variable = [entry for entry in selected if entry.variable_pricing]
    print(
        f"OpenRouter catalog: {len(catalog)} models | selected {len(selected)} | "
        f"excluded {len(excluded)} | probes: {', '.join(args.probes)}"
    )
    print(f"Worst-case spend if every probe hits its token ceiling: ${ceiling:.4f}")
    if variable:
        print(
            f"  (excludes {len(variable)} variable-priced router model"
            f"{'s' if len(variable) > 1 else ''}: "
            f"{', '.join(entry.id for entry in variable)})"
        )
    print()

    if args.dry_run:
        for entry in selected:
            caps = []
            if entry.claims_structured:
                caps.append("structured")
            if entry.claims_tools:
                caps.append("tools")
            print(f"  {entry.platform_id:<62} claims: {', '.join(caps) or 'text only'}")
        return 0

    if not settings.openrouter_api_key:
        print("OPENROUTER_API_KEY is not set — cannot probe.", file=sys.stderr)
        return 2

    try:
        results = await sweep(selected, args)
    except FatalProviderError as error:
        print(f"\nAborted — account-level OpenRouter failure: {error}", file=sys.stderr)
        return 2

    return report(results, excluded, args)


def main() -> int:
    """Compute the main.

    Returns:
        int: The result.
    """
    return asyncio.run(run(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
