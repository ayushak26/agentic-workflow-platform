"""Prometheus metrics for the agentic workflow platform.

Two-layer observability (the proposal's framing):
  - This module is the SYSTEM layer (operators). Prometheus scrapes /metrics,
    Grafana renders it in the Operator Console.
  - The WORKFLOW layer (end users) is the WebSocket event bus from Phase 9.

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