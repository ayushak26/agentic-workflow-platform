"""App-side client for the snippet-runner sidecar (app/runtime/snippet_daemon.py).

One request per connection over a Unix domain socket shared with the
sidecar via a volume (docker-compose.yml's `snippet-runner` service has
`network_mode: none` — there is no TCP port to reach it on, by design).
`PythonSnippetAgent` (app/nodes/python_snippet.py) is the only caller in a
running workflow; preflight's own probe() call is the other.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from app.runtime.snippet_protocol import read_message

#: How much longer the client waits than the snippet's own requested
#: timeout, before giving up on the sidecar entirely — covers queueing/
#: connection overhead, not snippet execution time (the daemon enforces
#: that deadline itself and always responds, timeout or not).
_CLIENT_TIMEOUT_SLACK_SECONDS = 10.0


class SnippetRunnerUnavailable(RuntimeError):
    """The sidecar could not be reached at all — refused connection, no
    socket file, or it never responded. Distinct from a snippet that ran
    and failed on its own terms."""


class SnippetRunnerClient:
    """Provides the SnippetRunnerClient behaviour."""
    def __init__(self, socket_path: str) -> None:
        """Initialize the SnippetRunnerClient.

        Args:
            socket_path (str): The socket path.
        """
        self._socket_path = socket_path

    async def run(
        self,
        code: str,
        inputs: dict[str, Any],
        *,
        timeout_seconds: float,
        memory_mb: int,
    ) -> dict[str, Any]:
        """Run the result.

        Args:
            code (str): The code.
            inputs (dict[str, Any]): Workflow input mapping.
            timeout_seconds (float): Timeout in seconds.
            memory_mb (int): The memory mb.

        Returns:
            dict[str, Any]: The result.
        """
        request = {
            "code": code,
            "inputs": inputs,
            "timeout_seconds": timeout_seconds,
            "memory_mb": memory_mb,
        }
        return await self._call(request, timeout_seconds + _CLIENT_TIMEOUT_SLACK_SECONDS)

    async def probe(self) -> None:
        """Preflight health check (SNIPPET_RUNNER_UNAVAILABLE) — a real,
        trivial round trip, not just "did connect() succeed", since a
        listening-but-wedged daemon is just as unusable."""
        result = await self._call(
            {"code": "output['ok'] = True", "inputs": {}, "timeout_seconds": 5, "memory_mb": 32},
            timeout_seconds=8.0,
        )
        if result.get("status") != "ok" or result.get("output", {}).get("ok") is not True:
            raise SnippetRunnerUnavailable(
                f"Snippet runner responded but not correctly: {result}"
            )

    async def _call(self, request: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
        """Internal helper for the call step.

        Args:
            request (dict[str, Any]): Incoming FastAPI request.
            timeout_seconds (float): Timeout in seconds.

        Returns:
            dict[str, Any]: The result.
        """
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(self._socket_path), timeout=5.0,
            )
        except (OSError, TimeoutError) as exc:
            raise SnippetRunnerUnavailable(
                f"Could not reach the snippet runner at {self._socket_path}: {exc}"
            ) from exc

        try:
            writer.write(json.dumps(request).encode("utf-8"))
            writer.write_eof()
            await writer.drain()
            try:
                raw = await asyncio.wait_for(read_message(reader), timeout=timeout_seconds)
            except TimeoutError as exc:
                raise SnippetRunnerUnavailable(
                    "Snippet runner did not respond in time — it may be "
                    "overloaded or stuck."
                ) from exc
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

        try:
            return json.loads(raw.decode("utf-8", errors="replace"))
        except Exception as exc:
            raise SnippetRunnerUnavailable(
                f"Snippet runner returned an unparseable response: {exc}"
            ) from exc
