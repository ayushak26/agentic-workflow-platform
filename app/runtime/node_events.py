"""Helpers for live event emission in the workflow runtime.

The compiler emits events inline inside _make_runtime_fn; these helpers are
the bits we want to keep testable in isolation.
"""
from __future__ import annotations
from typing import Any

# Keys we never include in event payloads sent to the browser.
# Alex SoW 3.1 — agent instructions must be 100% hidden from end users.
SENSITIVE_KEYS = {"prompt_template", "system_prompt", "instructions", "api_key", "secret"}
PREVIEW_CHARS = 240


def sanitize_preview(value: Any) -> str:
    """Short, safe preview for the Cockpit card. Strips sensitive keys, truncates strings,
    summarizes lists and nested dicts. The full output stays on the server."""
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for k, v in value.items():
            if k in SENSITIVE_KEYS:
                continue
            if isinstance(v, str):
                safe[k] = v[:PREVIEW_CHARS] + ("…" if len(v) > PREVIEW_CHARS else "")
            elif isinstance(v, (int, float, bool)) or v is None:
                safe[k] = v
            elif isinstance(v, list):
                safe[k] = f"<list, {len(v)} items>"
            elif isinstance(v, dict):
                safe[k] = f"<dict, keys: {list(v.keys())[:5]}>"
            else:
                safe[k] = f"<{type(v).__name__}>"
        return str(safe)[: PREVIEW_CHARS * 2]
    if isinstance(value, str):
        return value[:PREVIEW_CHARS] + ("…" if len(value) > PREVIEW_CHARS else "")
    return f"<{type(value).__name__}>"


def is_graph_interrupt(exc: BaseException) -> bool:
    """LangGraph's interrupt class has moved between versions. Match by name
    so we're not coupled to a specific import path."""
    cls = type(exc)
    return cls.__name__ in {"GraphInterrupt", "Interrupt"} or any(
        b.__name__ in {"GraphInterrupt", "Interrupt"} for b in cls.__mro__
    )


def interrupt_payload(exc: BaseException) -> Any:
    """Recover the actual value passed to ``langgraph.types.interrupt(...)``.

    ``GraphInterrupt.__init__`` stores ``(interrupts,)`` in ``exc.args``, where
    ``interrupts`` is a sequence of ``langgraph.types.Interrupt`` objects, each
    carrying the real payload on ``.value``. Passing ``exc.args`` itself
    through ``sanitize_preview`` only ever sees a bare tuple and collapses to
    the useless string ``"<tuple>"`` — this pulls out the one thing a durable
    checkpoint (and a later "what's this run waiting on?" read) actually
    needs: the HITL node's real question/content/allowed_actions dict.
    """
    args = getattr(exc, "args", None) or ()
    if not args:
        return None
    interrupts = args[0]
    if not interrupts:
        return None
    first = interrupts[0]
    return getattr(first, "value", None)