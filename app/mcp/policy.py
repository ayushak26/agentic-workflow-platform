"""What may be called, by whom, and whether a person has to see it first.

Every MCP tool invocation passes through this gate:

    server allowed? → tool allowed? → role allowed? → read/write policy?
    → human approval satisfied? → execute

The reason this is a module and not a check inside the node: a language model
must never be the thing deciding whether a CRM write is acceptable. The model
proposes; the deployment's policy disposes. That ordering is the entire safety
argument for letting a workflow touch a business system at all.

Classification precedence, strongest first:

1.  **Deployment policy** (`tool_policies` in the server registry). The
    operator's stated fact about the tool.
2.  **Server annotations** (`readOnlyHint`, `destructiveHint`). Useful, but a
    hint *from the thing being governed* — the MCP specification explicitly
    says clients must not rely on these for security decisions, so they can
    only ever make a tool look **more** dangerous here, never less.
3.  **Name heuristics**, as a last resort, biased toward caution.
4.  **`unknown`**, treated as a write.

That ordering is the point: a server that mislabels `delete_account` as
read-only cannot talk its way past the gate.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.mcp.registry import MCPServerConnection, OperationClass
from app.observability.logging import get_logger

log = get_logger(__name__)


#: Verbs that mean "this changes something", checked as whole words against a
#: normalised tool name.
_WRITE_VERBS = frozenset(
    {
        "create", "add", "insert", "new", "update", "patch", "modify", "edit",
        "set", "assign", "upsert", "send", "post", "submit", "close", "resolve",
        "convert", "merge", "link", "associate", "schedule", "book", "approve",
        "reject", "cancel", "qualify", "disqualify", "win", "lose", "append",
        "write", "save", "log",
    }
)

_DESTRUCTIVE_VERBS = frozenset(
    {"delete", "remove", "purge", "drop", "destroy", "erase", "wipe", "truncate"}
)

_READ_VERBS = frozenset(
    {
        "get", "fetch", "find", "search", "list", "read", "lookup", "query",
        "retrieve", "describe", "whoami", "who", "count", "check", "show",
    }
)


class MCPPolicyError(RuntimeError):
    """A tool call was refused by policy. Carries a message written for the
    workflow author, not for a log reader."""

    def __init__(self, message: str, *, code: str):
        self.code = code
        super().__init__(message)


class MCPApprovalRequired(MCPPolicyError):
    """A write was refused because no human review is guaranteed before it.

    Separate from a flat refusal because the fix is different and specific: add
    a Human Review step upstream, or state explicitly that this action may run
    unattended.
    """

    def __init__(self, message: str):
        super().__init__(message, code="MCP_APPROVAL_REQUIRED")


def _words(tool_name: str) -> list[str]:
    """Split a tool name into words across the naming styles servers use:
    `get-associated-opportunities`, `get_associated_opportunities`,
    `getAssociatedOpportunities`."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", tool_name)
    return [word for word in re.split(r"[^A-Za-z0-9]+", spaced.lower()) if word]


def classify_by_name(tool_name: str) -> OperationClass:
    """Best-effort classification from the tool's name alone.

    Deliberately biased: a destructive verb anywhere wins, then a write verb
    anywhere, and only a name that *starts* with a read verb counts as a read.
    `get_and_update_account` must not read as a read because it starts with
    "get".
    """
    words = _words(tool_name)
    if not words:
        return "unknown"
    if any(word in _DESTRUCTIVE_VERBS for word in words):
        return "destructive"
    if any(word in _WRITE_VERBS for word in words):
        return "write"
    if words[0] in _READ_VERBS:
        return "read"
    return "unknown"


def classify_by_annotations(annotations: Any) -> OperationClass | None:
    """Read the server's own hints, if it declared any.

    Only ever used to *raise* the classification (see module docstring): a
    `destructiveHint` is believed, a `readOnlyHint` is treated as a claim that
    still has to survive the other signals.
    """
    if annotations is None:
        return None
    destructive = getattr(annotations, "destructiveHint", None)
    read_only = getattr(annotations, "readOnlyHint", None)
    if destructive is True:
        return "destructive"
    if read_only is True:
        return "read"
    if read_only is False:
        return "write"
    return None


_SEVERITY: dict[OperationClass, int] = {
    "read": 0,
    "unknown": 1,
    "write": 2,
    "destructive": 3,
}


def classify_tool(
    tool_name: str,
    *,
    connection: MCPServerConnection | None = None,
    annotations: Any = None,
) -> OperationClass:
    """The platform's classification of one tool."""
    # 1. Deployment policy is authoritative and short-circuits everything.
    if connection is not None:
        declared = connection.policy_for(tool_name).operation
        if declared is not None:
            return declared

    from_name = classify_by_name(tool_name)
    from_annotations = classify_by_annotations(annotations)
    if from_annotations is None:
        return from_name

    # 2-3. Otherwise take the more cautious of the two signals. A server
    # claiming read-only for something named `delete_*` does not get to win.
    return max(
        (from_name, from_annotations),
        key=lambda item: _SEVERITY.get(item, 1),
    )


def is_write(operation: OperationClass) -> bool:
    """Anything not provably a read is treated as a write.

    `unknown` counts. A tool nobody has classified is exactly the one that
    should not run unattended against a customer's CRM.
    """
    return operation != "read"


@dataclass(frozen=True)
class PolicyDecision:
    """The outcome of the gate, kept as data so it can be logged, shown in the
    Builder, and asserted on in tests."""

    allowed: bool
    operation: OperationClass
    requires_approval: bool
    reason: str = ""
    code: str = ""

    def raise_if_denied(self) -> None:
        if self.allowed:
            return
        if self.code == "MCP_APPROVAL_REQUIRED":
            raise MCPApprovalRequired(self.reason)
        raise MCPPolicyError(self.reason, code=self.code or "MCP_TOOL_DENIED")


def evaluate(
    *,
    connection: MCPServerConnection,
    tool_name: str,
    annotations: Any = None,
    user_role: str | None = None,
    approval_satisfied: bool = False,
) -> PolicyDecision:
    """Run the gate for one tool call.

    `approval_satisfied` is the workflow's answer to "is a human review
    guaranteed before this step?" — computed from the graph by preflight and
    from the run's own history at execution time, never asserted by the node's
    own config alone.
    """
    operation = classify_tool(
        tool_name, connection=connection, annotations=annotations
    )
    tool_policy = connection.policy_for(tool_name)

    if not connection.permits_tool(tool_name):
        return PolicyDecision(
            allowed=False,
            operation=operation,
            requires_approval=False,
            code="MCP_TOOL_NOT_ALLOWED",
            reason=(
                f"Tool {tool_name!r} is not permitted on connection "
                f"{connection.label!r}. Permitted: "
                f"{sorted(connection.tool_allowlist) or 'all except the denylist'}."
            ),
        )

    permitted_roles = tool_policy.allowed_roles or connection.allowed_roles
    if permitted_roles and (user_role or "") not in permitted_roles:
        return PolicyDecision(
            allowed=False,
            operation=operation,
            requires_approval=False,
            code="MCP_ROLE_NOT_ALLOWED",
            reason=(
                f"Role {user_role or 'unknown'!r} may not call {tool_name!r} on "
                f"{connection.label!r}. Permitted roles: {sorted(permitted_roles)}."
            ),
        )

    writing = is_write(operation)
    if not writing:
        return PolicyDecision(
            allowed=True, operation=operation, requires_approval=False
        )

    if connection.write_policy == "read_only":
        return PolicyDecision(
            allowed=False,
            operation=operation,
            requires_approval=False,
            code="MCP_WRITE_NOT_PERMITTED",
            reason=(
                f"Connection {connection.label!r} is configured read-only, so "
                f"{tool_name!r} ({operation}) cannot run. A workflow cannot "
                "grant itself write access."
            ),
        )

    requires_approval = (
        tool_policy.requires_approval
        if tool_policy.requires_approval is not None
        else connection.write_policy == "require_approval"
    )

    if requires_approval and not approval_satisfied:
        return PolicyDecision(
            allowed=False,
            operation=operation,
            requires_approval=True,
            code="MCP_APPROVAL_REQUIRED",
            reason=(
                f"{tool_name!r} changes data in {connection.label!r} and needs a "
                "human decision first. Add a Human Review step upstream, or set "
                "the connection's write policy to allow unattended writes if "
                "that is the decision you want to make."
            ),
        )

    return PolicyDecision(
        allowed=True,
        operation=operation,
        requires_approval=requires_approval,
    )
