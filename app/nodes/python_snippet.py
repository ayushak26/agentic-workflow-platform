"""PythonSnippetAgent — write-and-run a Python snippet as a workflow step.

Runs the snippet in a genuinely isolated child process inside the
network-isolated snippet-runner sidecar (app/runtime/snippet_daemon.py,
docker-compose.yml's `snippet-runner` service — `network_mode: none`,
non-root, `cap_drop: [ALL]`), reached over a Unix socket
(app/runtime/snippet_client.py) — never executed in this process. That
distinction is not incidental: a naive in-process `exec()` was tried and
empirically found to leak a real API key out of `app.config` via
`import app.config`, freeze the whole application's event loop for the
duration of a CPU-bound loop, and be killable only by killing the entire
API process — none of which are true of the sidecar.

The snippet reads its inputs from a plain `inputs` dict and writes its
result into a plain `output` dict — both already bound in its execution
namespace by the sidecar's own bootstrap (app/runtime/snippet_protocol.py);
the snippet does not need to (and cannot) import anything of this platform's
own to use them.
"""
from __future__ import annotations

import ast
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.observability.logging import get_logger
from app.runtime.field_schema import FieldSpec
from app.runtime.snippet_client import SnippetRunnerUnavailable

log = get_logger(__name__)


class PythonSnippetConfig(BaseModel):
    """Pydantic model defining the PythonSnippetConfig shape.

    Attributes:
        code (str).
        input_fields (dict[str, Any]).
        output_fields (list[FieldSpec]).
        timeout_seconds (float).
        memory_mb (int).
        max_output_bytes (int).
        fail_on_error (bool).
    """
    model_config = ConfigDict(extra="forbid")

    code: str = Field(
        description=(
            "Python source. Reads inputs from the `inputs` dict; writes "
            "results into the `output` dict — both are already bound, no "
            "import needed."
        ),
    )
    #: name -> resolved value, same convention as SubprocessConfig.inputs —
    #: the compiler has already template-resolved every value by the time
    #: this config is validated.
    input_fields: dict[str, Any] = Field(default_factory=dict)
    #: The declared shape of `output` — what preflight can authorise as
    #: `result.<field>` template references, and what the Builder's field
    #: picker offers downstream. An empty list means `result` stays a
    #: generic, unchecked object.
    output_fields: list[FieldSpec] = Field(default_factory=list)
    timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    memory_mb: int = Field(default=128, ge=16, le=1024)
    max_output_bytes: int = Field(default=200_000, ge=1_000, le=2_000_000)
    fail_on_error: bool = Field(
        default=True,
        description="When off, a snippet failure becomes a routable status instead of stopping the run.",
    )


class PythonSnippetInput(BaseModel):
    """Pydantic model defining the PythonSnippetInput shape."""
    pass


class PythonSnippetOutput(BaseModel):
    """Pydantic model defining the PythonSnippetOutput shape.

    Attributes:
        status (str).
        result (dict[str, Any]).
        stdout (str).
        stderr (str).
        error (str | None).
        duration_s (float).
    """
    status: str = "ok"  # ok | error | timeout | limit_exceeded | output_too_large
    result: dict[str, Any] = Field(default_factory=dict)
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    duration_s: float = 0.0


@NodeRegistry.register
class PythonSnippetAgent(NodeType):
    """Workflow node type implementing the PythonSnippetAgent capability.

    Attributes:
        family (ClassVar[str]).
        execution_kind (ClassVar[str]).
        about (ClassVar[dict[str, Any]]).
    """
    type_name = "PythonSnippetAgent"
    description = (
        "Run a short Python snippet as a workflow step, in an isolated "
        "sandbox with no network access and no access to this platform's "
        "own code or secrets."
    )
    input_schema = PythonSnippetInput
    output_schema = PythonSnippetOutput
    config_schema = PythonSnippetConfig

    family: ClassVar[str] = "specialized"
    execution_kind: ClassVar[str] = "deterministic"
    about: ClassVar[dict[str, Any]] = {
        "what": "Runs a Python snippet in an isolated sandbox and returns whatever it wrote to `output`.",
        "why": (
            "Some steps are just a few lines of real code — a calculation, "
            "a reshape a Transform's operations don't cover — and forcing "
            "that through an LLM call would be slower, costlier, and less "
            "predictable than just running it."
        ),
        "receives": "Named input values, bound as the `inputs` dict inside the snippet.",
        "produces": "result (whatever the snippet wrote to `output`), plus stdout/stderr for debugging.",
        "uses_ai": False,
        "external_action": False,
        "safety": (
            "Runs in a network-isolated sandbox with no filesystem access "
            "beyond its own scratch directory, no access to this "
            "platform's own environment variables or code, and a hard "
            "timeout. It cannot read a real secret or reach another system."
        ),
    }

    @classmethod
    def required_services(cls, config: dict[str, Any]) -> set[str]:
        """Compute the required services.

        Args:
            config (dict[str, Any]): Node configuration mapping.

        Returns:
            set[str]: The services.
        """
        return {"python_runner"}

    @classmethod
    def preflight_static_output_values(cls, config: dict[str, Any]) -> dict[str, Any]:
        # No declared output_fields means `result` is never populated with
        # any provable field — a bare {{...result...}} reference (not a
        # sub-path) resolves to {} exactly like TransformAgent's own
        # no-schema case.
        """Compute the preflight static output values.

        Args:
            config (dict[str, Any]): Node configuration mapping.

        Returns:
            dict[str, Any]: The static output values.
        """
        if not (config.get("output_fields") or []):
            return {"result": {}}
        return {}

    async def run(self, state, resolved_config: dict[str, Any]) -> dict[str, Any]:
        """Run the result.

        Args:
            state: Current workflow state.
            resolved_config (dict[str, Any]): Configuration after template resolution.

        Returns:
            dict[str, Any]: The result.
        """
        cfg = PythonSnippetConfig(**resolved_config)
        runner = self.services.get("python_runner")
        if runner is None:
            raise RuntimeError(
                f"PythonSnippetAgent '{self.node_id}' needs the "
                "python_runner service (the snippet-runner sidecar), which "
                "is not configured in this deployment."
            )

        try:
            response = await runner.run(
                cfg.code,
                cfg.input_fields,
                timeout_seconds=cfg.timeout_seconds,
                memory_mb=cfg.memory_mb,
            )
        except SnippetRunnerUnavailable as error:
            return self._failure(cfg, "SNIPPET_RUNNER_UNAVAILABLE", str(error))

        status = response.get("status", "error")
        if status != "ok":
            return self._failure(
                cfg, status.upper(),
                response.get("error") or "The snippet did not complete successfully.",
                stdout=response.get("stdout", ""),
                stderr=response.get("stderr", ""),
                duration_s=float(response.get("duration_s") or 0.0),
            )

        result = response.get("output") or {}
        serialised_len = len(str(result))
        if serialised_len > cfg.max_output_bytes:
            return self._failure(
                cfg, "OUTPUT_TOO_LARGE",
                f"The snippet's output ({serialised_len} bytes) exceeds "
                f"the {cfg.max_output_bytes}-byte limit.",
                stdout=response.get("stdout", ""),
                stderr=response.get("stderr", ""),
                duration_s=float(response.get("duration_s") or 0.0),
            )

        return {
            "status": "ok",
            "result": result,
            "stdout": response.get("stdout", ""),
            "stderr": response.get("stderr", ""),
            "error": None,
            "duration_s": float(response.get("duration_s") or 0.0),
        }

    def _failure(
        self, cfg: PythonSnippetConfig, code: str, message: str,
        *, stdout: str = "", stderr: str = "", duration_s: float = 0.0,
    ) -> dict[str, Any]:
        """Internal helper for the failure step.

        Args:
            cfg (PythonSnippetConfig): The cfg.
            code (str): The code.
            message (str): Message text.
            stdout (str): The stdout (optional, default '').
            stderr (str): The stderr (optional, default '').
            duration_s (float): The duration s (optional, default 0.0).

        Returns:
            dict[str, Any]: The result.
        """
        log.warning("python_snippet.failed", node_id=self.node_id, code=code)
        if cfg.fail_on_error:
            raise RuntimeError(
                f"PythonSnippetAgent '{self.node_id}' failed ({code}): {message}"
            )
        return {
            "status": code.lower(),
            "result": {},
            "stdout": stdout,
            "stderr": stderr,
            "error": message[:800],
            "duration_s": duration_s,
        }


#: Modules with no legitimate use inside an already-network-isolated,
#: no-filesystem sandbox — flagged as an authoring-time hint, never the
#: actual security boundary (that is network_mode: none + the rlimits in
#: snippet_daemon.py, which hold regardless of what a snippet imports).
SUSPICIOUS_IMPORTS = frozenset({"socket", "subprocess", "ctypes", "multiprocessing"})


def scan_snippet_for_warnings(code: str) -> list[str]:
    """AST-level authoring hints — never a security control. Returns plain
    English warning strings; the caller decides how to surface them."""
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError:
        return []

    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in SUSPICIOUS_IMPORTS:
                    found.add(root)
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".")[0]
            if root in SUSPICIOUS_IMPORTS:
                found.add(root)
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__") and node.attr.endswith("__"):
            found.add(f"dunder attribute {node.attr!r}")

    return [
        f"imports {name!r}, which has no effect in the sandbox (no network, no other processes to reach)"
        if name in SUSPICIOUS_IMPORTS else f"accesses {name}"
        for name in sorted(found)
    ]


def assigns_output(code: str) -> bool:
    """Best-effort: does this snippet's top level ever touch `output` at
    all? Catches the common "forgot to write anything" authoring mistake —
    not exhaustive (a conditional branch, a helper function) by design,
    since a false warning is cheap and a missed one costs nothing preflight
    doesn't already cover some other way."""
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError:
        return True  # the syntax check itself reports this; don't pile on

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "output":
            return True
    return False
