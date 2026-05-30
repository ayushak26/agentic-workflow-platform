"""Unit tests for app.runtime.node_events helpers.

Pure-function tests of sanitize_preview and is_graph_interrupt.
Integration tests for event emission through a real workflow run live in
tests/test_executor_events.py.
"""
import pytest

from app.runtime.node_events import (
    sanitize_preview,
    is_graph_interrupt,
    SENSITIVE_KEYS,
)


# ---------- sanitize_preview ----------

@pytest.mark.parametrize("key", sorted(SENSITIVE_KEYS))
def test_sanitize_strips_each_sensitive_key(key):
    """Every key listed in SENSITIVE_KEYS must be redacted at the top level.
    Parametrize so adding a new sensitive key automatically gets coverage."""
    out = sanitize_preview({key: "LEAK_ME", "safe": "kept"})
    assert "LEAK_ME" not in out
    assert "safe" in out


def test_sanitize_truncates_long_string_values_in_dict():
    long = "x" * 1000
    out = sanitize_preview({"text": long})
    assert "…" in out
    assert len(out) < 2000


def test_sanitize_truncates_long_top_level_string():
    out = sanitize_preview("x" * 1000)
    assert out.endswith("…")
    assert len(out) < 1000


def test_sanitize_passes_through_short_strings():
    assert sanitize_preview("hello") == "hello"


def test_sanitize_summarizes_lists():
    out = sanitize_preview({"chunks": [1, 2, 3, 4, 5]})
    assert "list, 5 items" in out


def test_sanitize_summarizes_nested_dicts():
    out = sanitize_preview({"meta": {"a": 1, "b": 2, "c": 3}})
    assert "dict, keys" in out


def test_sanitize_preserves_scalar_types():
    out = sanitize_preview({"count": 42, "ok": True, "ratio": 0.5, "empty": None})
    assert "42" in out
    assert "True" in out
    assert "0.5" in out
    assert "None" in out


def test_sanitize_handles_non_string_non_dict_top_level():
    """Lists, ints, None at the top level fall through to a type marker."""
    assert sanitize_preview(42) == "<int>"
    assert sanitize_preview([1, 2, 3]) == "<list>"
    assert sanitize_preview(None) == "<NoneType>"


def test_sanitize_empty_dict_is_safe():
    out = sanitize_preview({})
    assert out == "{}"


# ---------- is_graph_interrupt ----------

def test_is_graph_interrupt_matches_exact_name():
    class GraphInterrupt(BaseException):
        pass
    assert is_graph_interrupt(GraphInterrupt())


def test_is_graph_interrupt_matches_alternate_name():
    class Interrupt(BaseException):
        pass
    assert is_graph_interrupt(Interrupt())


def test_is_graph_interrupt_matches_subclass_via_mro():
    """A subclass of GraphInterrupt is still treated as an interrupt.
    Belt-and-suspenders for LangGraph versions that define interrupt subtypes."""
    class GraphInterrupt(BaseException):
        pass
    class CustomPause(GraphInterrupt):
        pass
    assert is_graph_interrupt(CustomPause())


def test_is_graph_interrupt_rejects_unrelated_exception():
    assert not is_graph_interrupt(ValueError("nope"))


def test_is_graph_interrupt_rejects_base_exception():
    assert not is_graph_interrupt(BaseException())


def test_is_graph_interrupt_rejects_similarly_named_class():
    """Defensive: a class named 'Interrupted' is not an interrupt — we match
    {'GraphInterrupt', 'Interrupt'} exactly, not by prefix."""
    class Interrupted(BaseException):
        pass
    assert not is_graph_interrupt(Interrupted())