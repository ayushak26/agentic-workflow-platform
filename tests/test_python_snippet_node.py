"""PythonSnippetAgent — the node's own contract, with a stub runner.

The real isolation properties (network_mode: none, rlimits, timeout+kill,
env stripping, import blocking) are covered by tests/test_snippet_runner.py,
which runs the actual sidecar daemon as a real subprocess over a real Unix
socket. This file is the node-level contract: given whatever the runner
returned, does PythonSnippetAgent shape its own output correctly, and does
it respect fail_on_error the same way every other lookup node in this
platform does.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.nodes.python_snippet import (
    PythonSnippetAgent,
    assigns_output,
    scan_snippet_for_warnings,
)
from app.runtime.snippet_client import SnippetRunnerUnavailable


class StubRunner:
    def __init__(self, response: dict[str, Any] | Exception):
        self._response = response
        self.calls: list[dict[str, Any]] = []

    async def run(self, code, inputs, *, timeout_seconds, memory_mb):
        self.calls.append({
            "code": code, "inputs": inputs,
            "timeout_seconds": timeout_seconds, "memory_mb": memory_mb,
        })
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def node(config: dict, runner) -> PythonSnippetAgent:
    instance = PythonSnippetAgent("snip", config)
    instance.services = {"python_runner": runner}
    return instance


async def run(instance: PythonSnippetAgent):
    state = {"inputs": {"SYSTEM.run_id": "run-1"}, "node_outputs": {}}
    return await instance.run(state, instance.config.model_dump())


BASE_CONFIG = {"code": "output['x'] = 1", "input_fields": {"a": 1}}


class TestSuccess:
    @pytest.mark.asyncio
    async def test_shapes_a_successful_response(self):
        runner = StubRunner({
            "status": "ok", "output": {"x": 2}, "stdout": "hi\n", "stderr": "", "error": None, "duration_s": 0.1,
        })
        result = await run(node(BASE_CONFIG, runner))
        assert result == {
            "status": "ok", "result": {"x": 2}, "stdout": "hi\n", "stderr": "",
            "error": None, "duration_s": 0.1,
        }
        assert runner.calls == [{
            "code": "output['x'] = 1", "inputs": {"a": 1},
            "timeout_seconds": 10.0, "memory_mb": 128,
        }]

    @pytest.mark.asyncio
    async def test_output_over_the_byte_limit_is_refused(self):
        runner = StubRunner({
            "status": "ok", "output": {"big": "x" * 2000}, "stdout": "", "stderr": "", "error": None, "duration_s": 0.1,
        })
        instance = node({**BASE_CONFIG, "max_output_bytes": 1000, "fail_on_error": False}, runner)
        result = await run(instance)
        assert result["status"] == "output_too_large"
        assert result["result"] == {}


class TestFailure:
    @pytest.mark.asyncio
    async def test_fail_on_error_true_raises_on_a_runner_side_failure(self):
        runner = StubRunner({
            "status": "error", "output": {}, "stdout": "", "stderr": "boom", "error": "ValueError: boom", "duration_s": 0.05,
        })
        with pytest.raises(RuntimeError, match="ERROR"):
            await run(node(BASE_CONFIG, runner))

    @pytest.mark.asyncio
    async def test_fail_on_error_false_returns_a_routable_status(self):
        runner = StubRunner({
            "status": "timeout", "output": {}, "stdout": "", "stderr": "", "error": "Did not finish within 5s.", "duration_s": 5.0,
        })
        result = await run(node({**BASE_CONFIG, "fail_on_error": False}, runner))
        assert result["status"] == "timeout"
        assert result["result"] == {}
        assert "Did not finish" in result["error"]

    @pytest.mark.asyncio
    async def test_runner_unavailable_is_reported_not_a_bare_crash(self):
        runner = StubRunner(SnippetRunnerUnavailable("sidecar unreachable"))
        instance = node({**BASE_CONFIG, "fail_on_error": False}, runner)
        result = await run(instance)
        assert result["status"] == "snippet_runner_unavailable"
        assert "unreachable" in result["error"]

    @pytest.mark.asyncio
    async def test_missing_runner_service_is_a_clear_error(self):
        instance = PythonSnippetAgent("snip", BASE_CONFIG)
        instance.services = {}
        with pytest.raises(RuntimeError, match="python_runner"):
            await run(instance)


class TestStaticScan:
    def test_flags_a_suspicious_import(self):
        warnings = scan_snippet_for_warnings("import socket\noutput['x'] = 1")
        assert any("socket" in w for w in warnings)

    def test_clean_code_has_no_warnings(self):
        assert scan_snippet_for_warnings("output['x'] = inputs.get('a', 0) + 1") == []

    def test_detects_a_snippet_that_never_touches_output(self):
        assert assigns_output("x = 1 + 1") is False
        assert assigns_output("output['x'] = 1") is True
