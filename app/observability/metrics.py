"""Prometheus metrics for the agentic workflow platform.

Two-layer observability (the proposal's framing):
  - This module is the SYSTEM layer (operators). Prometheus scrapes /metrics,
    Grafana renders it in the Operator Console.
  - The WORKFLOW layer (end users) is the SSE event bus.

Metric type choices — the reasoning you defend in interviews:
  - Histogram for latency: we care about the DISTRIBUTION (p50/p95/p99), not just
    a running total or a current value. Histograms bucket observations so Grafana
    can compute quantiles. A Counter only ever goes up (totals); a Gauge is a
    single instantaneous value (e.g. in-flight requests).
  - Counter for tokens / errors / runs: monotonically increasing totals. Rate is
    derived in Grafana with rate(...) over a time window.
  - Labels are kept LOW-cardinality on purpose. We label by node_type and a
    bounded status, NEVER by run_id or session_id — unbounded label values blow
    up Prometheus memory (one time series per distinct label combination).
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Iterator

from prometheus_client import Counter, Gauge, Histogram

# ── Workflow / node execution ───────────────────────────────────────────────

NODE_LATENCY = Histogram(
    "awp_node_execution_seconds",
    "Wall-clock time to execute a single workflow node.",
    labelnames=("node_type",),
    # Buckets tuned for LLM-bound work: most nodes take 0.5s–30s.
    buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30, 60, 120),
)

NODE_RUNS = Counter(
    "awp_node_runs_total",
    "Count of node executions, partitioned by outcome.",
    labelnames=("node_type", "status"),  # status: success | error | paused
)

WORKFLOW_LATENCY = Histogram(
    "awp_workflow_run_seconds",
    "End-to-end wall-clock time for a full workflow run.",
    labelnames=("workflow",),
    buckets=(1, 5, 10, 30, 60, 120, 300, 600),
)

WORKFLOW_RUNS = Counter(
    "awp_workflow_runs_total",
    "Count of workflow runs, partitioned by outcome.",
    labelnames=("workflow", "status"),  # success | error
)

NODES_IN_FLIGHT = Gauge(
    "awp_nodes_in_flight",
    "Number of nodes currently executing (shows parallel-branch fan-out).",
)

# ── LLM cost / usage ─────────────────────────────────────────────────────────

LLM_TOKENS = Counter(
    "awp_llm_tokens_total",
    "LLM tokens consumed, partitioned by model and direction.",
    labelnames=("model", "direction"),  # direction: prompt | completion
)

LLM_CALLS = Counter(
    "awp_llm_calls_total",
    "LLM gateway calls, partitioned by model and outcome.",
    labelnames=("model", "status"),  # success | error
)

LLM_RETRIES = Counter(
    "awp_llm_retries_total",
    "Transient LLM calls retried by the provider-neutral resilience policy.",
    labelnames=("model", "reason"),
)

LLM_FAILOVERS = Counter(
    "awp_llm_failovers_total",
    "LLM calls moved from a primary model to its mapped fallback.",
    labelnames=("from_model", "to_model"),
)

LLM_CACHE_TOKENS = Counter(
    "awp_llm_cache_tokens_total",
    "Prompt-cache tokens, partitioned by model and direction. "
    "'write' is Anthropic-only (cache_creation_input_tokens); 'read' covers "
    "both Anthropic cache hits and OpenAI's automatic prompt-cache hits.",
    labelnames=("model", "direction"),  # direction: write | read
)

# ── HTTP layer (middleware) ──────────────────────────────────────────────────

HTTP_REQUESTS = Counter(
    "awp_http_requests_total",
    "HTTP requests, partitioned by method, route template, and status.",
    # route is the ROUTE TEMPLATE (/api/workflows/{run_id}), never the filled
    # path — keeps cardinality bounded by the number of routes, not requests.
    labelnames=("method", "route", "status"),
)

HTTP_REQUEST_LATENCY = Histogram(
    "awp_http_request_seconds",
    "Wall-clock latency of an HTTP request, by method and route template.",
    labelnames=("method", "route"),
    # Web-tier buckets: most API calls are sub-second; SSE/render can be slow.
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10),
)

RATE_LIMIT_REJECTIONS = Counter(
    "awp_rate_limit_rejections_total",
    "Requests rejected with HTTP 429 by the Redis rate limiter.",
    labelnames=("scope",),  # api | auth
)

# ── Guardrails (security) ────────────────────────────────────────────────────

GUARDRAIL_EVENTS = Counter(
    "awp_guardrail_events_total",
    "Guardrail actions on model I/O, by direction and outcome.",
    # direction: input | output
    # outcome:   blocked | redact | block | redacted  (bounded set)
    labelnames=("direction", "outcome"),
)

# ── Confidential entity protection (Phase 1) ────────────────────────────────

ENTITY_TOKENIZER_EVENTS = Counter(
    "awp_entity_tokenizer_events_total",
    "Entity-tokenization response validation events, by outcome.",
    # outcome: unresolved_placeholder | response_leak_detected  (bounded set)
    labelnames=("outcome",),
)

# ── LLM semantic cache ───────────────────────────────────────────────────────

LLM_CACHE = Counter(
    "awp_llm_cache_events_total",
    "Semantic LLM cache lookups, partitioned by result.",
    labelnames=("status",),  # hit | miss | error
)

# ── Knowledge Studio: retrieval / ingestion ─────────────────────────────────
#
# Same low-cardinality discipline as above: strategy/stage/status only.
# Never label with query text, collection_id, document_id, filename or user —
# those are unbounded and belong in the retrieval trace, not a metric label.

RAG_RETRIEVAL_REQUESTS = Counter(
    "awp_rag_retrieval_requests_total",
    "Canonical RetrievalService.retrieve() calls, partitioned by outcome.",
    labelnames=("strategy", "status"),  # status: success | failed
)

RAG_RETRIEVAL_LATENCY = Histogram(
    "awp_rag_retrieval_latency_seconds",
    "End-to-end latency of one retrieve() call.",
    labelnames=("strategy",),
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20),
)

RAG_CANDIDATES = Counter(
    "awp_rag_candidates_total",
    "Candidate chunks surfaced before reranking, partitioned by strategy.",
    labelnames=("strategy",),
)

RAG_CONTEXT_CHUNKS = Counter(
    "awp_rag_context_chunks_total",
    "Chunks that survived into the final assembled context.",
    labelnames=("strategy",),
)

INGESTION_JOBS = Counter(
    "awp_ingestion_jobs_total",
    "Completed ingestion jobs, partitioned by terminal status.",
    labelnames=("status",),
)

INGESTION_DOCUMENTS = Counter(
    "awp_ingestion_documents_total",
    "Per-document ingestion outcomes.",
    labelnames=("status",),  # completed | failed
)

INGESTION_FAILURES = Counter(
    "awp_ingestion_failures_total",
    "Document ingestion failures, partitioned by the stage they failed in.",
    labelnames=("stage",),
)


@contextmanager
def track_node(node_type: str) -> Iterator[None]:
    """Time a node, record its outcome, and track in-flight count.

    Usage in the executor:
        with track_node(node.type):
            result = await node.run(...)

    On exception: records status='error' and re-raises (metrics never swallow
    errors). On success: records status='success'.
    """
    NODES_IN_FLIGHT.inc()
    start = time.perf_counter()
    status = "success"
    try:
        yield
    except Exception:
        status = "error"
        raise
    finally:
        elapsed = time.perf_counter() - start
        NODE_LATENCY.labels(node_type=node_type).observe(elapsed)
        NODE_RUNS.labels(node_type=node_type, status=status).inc()
        NODES_IN_FLIGHT.dec()


def record_paused(node_type: str) -> None:
    """HITL pause is not an error and not a success — its own outcome.

    Called from the HITL path so the success/error counts stay clean.
    """
    NODE_RUNS.labels(node_type=node_type, status="paused").inc()


def record_llm_usage(
    model: str, prompt_tokens: int, completion_tokens: int, ok: bool
) -> None:
    """Record token usage and call outcome. Called by the LLM gateway."""
    LLM_CALLS.labels(model=model, status="success" if ok else "error").inc()
    if prompt_tokens:
        LLM_TOKENS.labels(model=model, direction="prompt").inc(prompt_tokens)
    if completion_tokens:
        LLM_TOKENS.labels(model=model, direction="completion").inc(completion_tokens)