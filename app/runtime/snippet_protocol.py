"""Shared shape between the app and the snippet-runner sidecar.

Both sides of the Unix socket import this module — the daemon (running
inside the sidecar container, see snippet_daemon.py) to parse a request and
build a response, the client (running in the main app, see
snippet_client.py) to build a request and parse a response. One shape, one
place, so they cannot drift out of sync with each other.
"""
from __future__ import annotations

import asyncio
from typing import Any, Literal, TypedDict

#: Bounds applied everywhere a size shows up in this protocol — a request or
#: response larger than this is refused/truncated rather than trusted, since
#: the whole point of the sidecar is that its own resource use is bounded.
MAX_MESSAGE_BYTES = 2_000_000
MAX_CAPTURE_BYTES = 20_000


async def read_message(reader: asyncio.StreamReader, limit: int = MAX_MESSAGE_BYTES) -> bytes:
    """Read one EOF-terminated message, accumulating across as many socket
    reads as it takes.

    Both sides of this protocol send exactly one message per connection and
    then close (see snippet_daemon.py's `_handle` and snippet_client.py's
    `_call`) — a single `reader.read(limit)` call is NOT equivalent to that:
    asyncio's StreamReader returns as soon as *any* data is buffered rather
    than waiting for EOF, so a message that arrives across more than one
    underlying socket read gets silently truncated to its first chunk. That
    surfaced for real as `json.loads` failing with "Unterminated string" on
    a large `rag_context` value that crossed a buffer boundary.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await reader.read(65536)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > limit:
            break
    return b"".join(chunks)


class SnippetRequest(TypedDict):
    """Provides the SnippetRequest behaviour.

    Attributes:
        code (str).
        inputs (dict[str, Any]).
        timeout_seconds (float).
        memory_mb (int).
        max_output_bytes (int).
    """
    code: str
    inputs: dict[str, Any]
    timeout_seconds: float
    memory_mb: int
    max_output_bytes: int


SnippetStatus = Literal["ok", "error", "timeout", "limit_exceeded", "output_too_large", "crashed"]


class SnippetResponse(TypedDict, total=False):
    """Provides the SnippetResponse behaviour.

    Attributes:
        status (SnippetStatus).
        output (dict[str, Any]).
        stdout (str).
        stderr (str).
        error (str | None).
        duration_s (float).
    """
    status: SnippetStatus
    output: dict[str, Any]
    stdout: str
    stderr: str
    error: str | None
    duration_s: float


#: Executed inside the isolated child via `sys.executable -I -S -B -c
#: BOOTSTRAP` — a fixed template, never containing the author's own code
#: (that arrives separately, over stdin, as JSON). `-I` (isolated: ignores
#: PYTHONPATH/PYTHONHOME, drops the script directory and user site-packages
#: from sys.path) plus `-S` (skip the site module entirely) plus `-B` (no
#: .pyc) is what makes `import app.config` fail inside the snippet —
#: verified: raises ModuleNotFoundError, not merely "shouldn't work".
#:
#: The author's own print() output must never land in the same stream as
#: this bootstrap's final JSON result, so stdout/stderr are redirected to
#: in-memory buffers for the duration of the exec() and restored before the
#: real result is written to the real stdout — verified this correctly
#: separates a snippet's own prints from the structured result.
BOOTSTRAP_SOURCE = f"""
import sys, json, io

def _main():
    raw = sys.stdin.read()
    try:
        request = json.loads(raw)
    except Exception as exc:
        sys.stdout.write(json.dumps({{"status": "error", "output": {{}}, "stdout": "", "stderr": "", "error": f"invalid request: {{exc}}"}}))
        return

    code = request.get("code", "")
    inputs = request.get("inputs", {{}})
    max_capture = {MAX_CAPTURE_BYTES}

    real_stdout = sys.stdout
    captured_out = io.StringIO()
    captured_err = io.StringIO()
    sys.stdout = captured_out
    sys.stderr = captured_err

    namespace = {{"inputs": inputs, "output": {{}}}}
    status = "ok"
    error = None
    try:
        exec(compile(code, "<snippet>", "exec"), namespace)
    except BaseException as exc:
        status = "error"
        error = f"{{type(exc).__name__}}: {{exc}}"

    sys.stdout = real_stdout
    sys.stderr = sys.__stderr__

    output = namespace.get("output")
    if not isinstance(output, dict):
        output = {{}}

    result = {{
        "status": status,
        "output": output,
        "stdout": captured_out.getvalue()[:max_capture],
        "stderr": captured_err.getvalue()[:max_capture],
        "error": error,
    }}
    try:
        payload = json.dumps(result, default=str)
    except Exception as exc:
        payload = json.dumps({{"status": "error", "output": {{}}, "stdout": "", "stderr": "", "error": f"result not JSON-serialisable: {{exc}}"}})
    real_stdout.write(payload)
    real_stdout.flush()

_main()
"""
