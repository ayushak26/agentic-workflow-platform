"""The snippet-runner daemon and client, over a real Unix socket.

Runs app/runtime/snippet_daemon.py as a real subprocess (not mocked) and
talks to it through app/runtime/snippet_client.py exactly as
PythonSnippetAgent does — the actual isolation properties under test here
were each verified once already, empirically, before being written into the
daemon (see its own module docstring); this is the regression suite for
those same properties.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid

import pytest

from app.runtime.snippet_client import SnippetRunnerClient, SnippetRunnerUnavailable


@pytest.fixture(scope="module")
def daemon():
    socket_path = f"/tmp/pytest-snippet-runner-{uuid.uuid4().hex}.sock"
    env = {**os.environ, "SNIPPET_RUNNER_SOCKET_PATH": socket_path}
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.runtime.snippet_daemon"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 5.0
    while not os.path.exists(socket_path):
        if time.monotonic() > deadline:
            proc.terminate()
            raise RuntimeError("snippet daemon did not create its socket in time")
        time.sleep(0.05)
    try:
        yield socket_path
    finally:
        proc.terminate()
        proc.wait(timeout=5)
        if os.path.exists(socket_path):
            os.unlink(socket_path)


@pytest.fixture()
def client(daemon):
    return SnippetRunnerClient(daemon)


class TestBasicRoundTrip:
    @pytest.mark.asyncio
    async def test_probe_succeeds_against_a_healthy_daemon(self, client):
        await client.probe()  # raises on failure

    @pytest.mark.asyncio
    async def test_inputs_flow_in_and_output_flows_out(self, client):
        result = await client.run(
            "output['sum'] = inputs['a'] + inputs['b']",
            {"a": 3, "b": 4},
            timeout_seconds=5, memory_mb=64,
        )
        assert result["status"] == "ok"
        assert result["output"] == {"sum": 7}

    @pytest.mark.asyncio
    async def test_print_output_is_captured_separately_from_the_result(self, client):
        result = await client.run(
            "print('a diagnostic line')\noutput['x'] = 1",
            {}, timeout_seconds=5, memory_mb=64,
        )
        assert result["status"] == "ok"
        assert result["output"] == {"x": 1}
        assert "a diagnostic line" in result["stdout"]

    @pytest.mark.asyncio
    async def test_a_raised_exception_is_reported_not_crashed(self, client):
        result = await client.run("1 / 0", {}, timeout_seconds=5, memory_mb=64)
        assert result["status"] == "error"
        assert "ZeroDivisionError" in result["error"]

    @pytest.mark.asyncio
    async def test_output_defaults_to_empty_when_never_assigned(self, client):
        result = await client.run("x = 1 + 1", {}, timeout_seconds=5, memory_mb=64)
        assert result["status"] == "ok"
        assert result["output"] == {}


class TestContainment:
    """Each of these is a verified security property, not an assumption."""

    @pytest.mark.asyncio
    async def test_an_infinite_loop_is_killed_by_the_wall_clock_timeout(self, client):
        start = time.monotonic()
        result = await client.run("while True:\n    pass", {}, timeout_seconds=1, memory_mb=64)
        elapsed = time.monotonic() - start
        assert result["status"] == "timeout"
        assert elapsed < 4, "the daemon must actually kill the process, not just report a timeout eventually"

    @pytest.mark.asyncio
    async def test_a_real_secret_set_in_this_process_is_not_visible_to_the_snippet(self, client, monkeypatch):
        monkeypatch.setenv("PYTEST_SNIPPET_SECRET_PROBE", "leaked-if-you-see-this")
        result = await client.run(
            "import os\noutput['has_secret'] = 'PYTEST_SNIPPET_SECRET_PROBE' in os.environ",
            {}, timeout_seconds=5, memory_mb=64,
        )
        assert result["status"] == "ok"
        assert result["output"]["has_secret"] is False

    @pytest.mark.asyncio
    async def test_this_platforms_own_modules_are_not_importable(self, client):
        result = await client.run(
            "try:\n"
            "    import app.config\n"
            "    output['imported'] = True\n"
            "except Exception as exc:\n"
            "    output['imported'] = False\n"
            "    output['error_type'] = type(exc).__name__\n",
            {}, timeout_seconds=5, memory_mb=64,
        )
        assert result["status"] == "ok"
        assert result["output"]["imported"] is False
        assert result["output"]["error_type"] == "ModuleNotFoundError"

    @pytest.mark.asyncio
    async def test_forking_a_subprocess_is_blocked(self, client):
        result = await client.run(
            "import subprocess\n"
            "try:\n"
            "    subprocess.run(['echo', 'hi'])\n"
            "    output['forked'] = True\n"
            "except Exception:\n"
            "    output['forked'] = False\n",
            {}, timeout_seconds=5, memory_mb=64,
        )
        assert result["status"] == "ok"
        assert result["output"]["forked"] is False

    @pytest.mark.asyncio
    async def test_writing_a_file_is_blocked(self, client):
        result = await client.run(
            "try:\n"
            "    with open('should_not_exist.txt', 'w') as f:\n"
            "        f.write('x' * 10000)\n"
            "    output['wrote'] = True\n"
            "except Exception:\n"
            "    output['wrote'] = False\n",
            {}, timeout_seconds=5, memory_mb=64,
        )
        assert result["status"] == "ok"
        assert result["output"]["wrote"] is False


class TestUnreachable:
    @pytest.mark.asyncio
    async def test_a_nonexistent_socket_raises_a_clear_error(self):
        client = SnippetRunnerClient("/tmp/definitely-does-not-exist.sock")
        with pytest.raises(SnippetRunnerUnavailable):
            await client.probe()
