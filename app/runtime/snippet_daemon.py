"""The snippet-runner sidecar's own entrypoint.

Runs *inside* the network-isolated sidecar container (docker-compose.yml's
`snippet-runner` service — `network_mode: none`, `read_only`, non-root,
`cap_drop: [ALL]`), never inside the main app container. Listens on a Unix
domain socket (shared with the app via a volume, not a TCP port — there is
no network to listen on) for one request per connection: read a JSON
request until EOF, spawn one genuinely isolated child process per
snippet_protocol.BOOTSTRAP_SOURCE, write back one JSON response, close.

Every isolation property below was verified empirically against a real
subprocess before being written here (see this session's own investigation):
`env={}` blocks a snippet reading a real secret out of the environment;
`sys.executable -I -S -B` blocks `import app.<anything>`; `RLIMIT_NPROC=0`
blocks forking (and therefore `subprocess`); `RLIMIT_FSIZE=0` blocks writing
a file; a wall-clock `asyncio.wait_for` + `killpg` stops an infinite loop
that `RLIMIT_CPU` alone would not catch quickly enough for a request this
short-lived. `RLIMIT_AS` (memory) is real on Linux — the platform this
container actually runs on — but was verified to silently fail via
`setrlimit` on macOS, which is exactly why it is wrapped in its own
try/except here rather than assumed to always succeed.
"""
from __future__ import annotations

import asyncio
import json
import os
import resource
import signal
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from app.runtime.snippet_protocol import (
    BOOTSTRAP_SOURCE,
    MAX_MESSAGE_BYTES,
    SnippetRequest,
)

_DEFAULT_SOCKET_PATH = "/run/snippet-runner/snippet-runner.sock"
_MAX_TIMEOUT_SECONDS = 60.0
_MAX_MEMORY_MB = 1024


def _socket_path() -> str:
    return os.environ.get("SNIPPET_RUNNER_SOCKET_PATH", _DEFAULT_SOCKET_PATH)


def _clamped(request: dict[str, Any]) -> SnippetRequest:
    """Never trust the client's own limits past the sidecar's own ceiling —
    a compromised or buggy app process must not be able to ask this
    container to run something for an hour."""
    timeout_seconds = min(
        max(float(request.get("timeout_seconds", 10) or 10), 0.1), _MAX_TIMEOUT_SECONDS,
    )
    memory_mb = min(max(int(request.get("memory_mb", 128) or 128), 16), _MAX_MEMORY_MB)
    return {
        "code": str(request.get("code", "")),
        "inputs": request.get("inputs") if isinstance(request.get("inputs"), dict) else {},
        "timeout_seconds": timeout_seconds,
        "memory_mb": memory_mb,
        "max_output_bytes": 0,  # reserved, not currently separately enforced
    }


def _rlimit_preexec(memory_mb: int, timeout_seconds: float):
    """Runs in the child, after fork, before exec — this is real isolation,
    not the request's own JSON asking nicely. Each limit is independent and
    best-effort: one unsupported limit on a given platform (verified:
    RLIMIT_AS on macOS) must not prevent the others from being applied.
    """

    def setter() -> None:
        mem_bytes = memory_mb * 1024 * 1024
        # CPU time is a generous multiple of the wall-clock budget — the
        # real deadline is the wall-clock asyncio.wait_for below; this is
        # only a backstop against a process that somehow keeps running CPU
        # time after being (attempted to be) killed.
        cpu_seconds = max(1, int(timeout_seconds * 4))
        for limit, value in (
            (resource.RLIMIT_AS, (mem_bytes, mem_bytes)),
            (resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds)),
            (resource.RLIMIT_NPROC, (0, 0)),
            (resource.RLIMIT_FSIZE, (0, 0)),
        ):
            try:
                resource.setrlimit(limit, value)
            except (ValueError, OSError):
                pass

    return setter


async def _execute(request: SnippetRequest) -> dict[str, Any]:
    scratch = tempfile.mkdtemp(prefix="snippet-")
    started = time.monotonic()
    stdin_payload = json.dumps({"code": request["code"], "inputs": request["inputs"]}).encode(
        "utf-8", errors="replace"
    )

    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-I", "-S", "-B", "-c", BOOTSTRAP_SOURCE,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={},
        cwd=scratch,
        preexec_fn=_rlimit_preexec(request["memory_mb"], request["timeout_seconds"]),
        start_new_session=True,
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(stdin_payload), timeout=request["timeout_seconds"],
        )
    except TimeoutError:
        # A bounded read has no chance to run once the process is gone —
        # kill the whole process group (start_new_session=True made this
        # one), not just the direct child, since RLIMIT_NPROC should have
        # already blocked it from forking anything to escape into anyway.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await proc.wait()
        return {
            "status": "timeout",
            "output": {},
            "stdout": "",
            "stderr": "",
            "error": f"Did not finish within {request['timeout_seconds']}s.",
            "duration_s": time.monotonic() - started,
        }

    duration_s = time.monotonic() - started
    try:
        result = json.loads(stdout.decode("utf-8", errors="replace"))
    except Exception:
        # Verified this really happens (not just theoretical): a
        # MemoryError raised while the bootstrap is already mid-exception-
        # handling can produce no parseable output at all. Treat "the child
        # produced nothing readable" as a first-class expected outcome.
        return {
            "status": "crashed",
            "output": {},
            "stdout": stdout.decode("utf-8", errors="replace")[:2000],
            "stderr": stderr.decode("utf-8", errors="replace")[:2000],
            "error": "The snippet produced no parseable result (it may have been killed by a resource limit).",
            "duration_s": duration_s,
        }

    result["duration_s"] = duration_s
    return result


async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        raw = await reader.read(MAX_MESSAGE_BYTES)
        if not raw:
            # A bare connect-then-disconnect with no request — the
            # healthcheck above does exactly this to verify the socket is
            # listening. Nothing to respond to.
            return
        try:
            request = json.loads(raw.decode("utf-8", errors="replace"))
        except Exception as exc:
            response = {"status": "error", "output": {}, "stdout": "", "stderr": "", "error": f"invalid request: {exc}"}
        else:
            response = await _execute(_clamped(request))
        writer.write(json.dumps(response, default=str).encode("utf-8"))
        await writer.drain()
    except (ConnectionResetError, BrokenPipeError):
        # The caller went away before the response could be delivered —
        # nothing this side can do about that, and nothing worth logging as
        # an error (a client that times out and disconnects is expected
        # behaviour, not a daemon bug).
        pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def main() -> None:
    path = _socket_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    if os.path.exists(path):
        os.unlink(path)
    server = await asyncio.start_unix_server(_handle, path=path)
    os.chmod(path, 0o666)  # any process that can reach the shared volume may call in; the sidecar itself has no other listener
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
