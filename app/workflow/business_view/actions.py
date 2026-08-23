"""Typed, permission-checked Business View actions.

Two rules decide whether a button exists:

1. **It must be implementable.** Every `BusinessActionType` here maps either to
   a real durable handler (app/workflow/business_view/dispatch.py) or to a
   client-side navigation the UI genuinely performs. A control the platform
   cannot carry out is never emitted, so the screen has no decorative buttons.
2. **It must be valid now, for this person.** State gating (you cannot resume a
   run that is not paused) and permission gating (`workflow:run`) both happen
   here, on the server, so the UI never has to guess and cannot be talked into
   rendering something the backend would reject.

This module is pure: it builds action descriptions, it does not perform them.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.security.rbac import Role, has_permission
from app.workflow.business_view.models import BusinessAction, BusinessActionType
from app.workflow.business_view.runstate import RunView


@dataclass
class ActionContext:
    """Everything action availability depends on."""

    run: RunView
    role: Role

    @property
    def can_act(self) -> bool:
        """Whether this person may change the work item at all."""
        return has_permission(self.role, "workflow:run")

    @property
    def rerun_mode(self) -> str | None:
        """Which existing rerun primitive fits this run's state.

        `retry` reuses completed node results from a durable checkpoint and
        only exists for a failed run. `restart` re-runs from the original
        inputs and works from any status — the right answer for "recheck this
        now that I've corrected a fact". A run still in flight has nothing to
        rerun.
        """
        if self.run.run_status == "failed":
            return "retry"
        if self.run.run_status in ("completed", "rejected"):
            return "restart"
        return None


class ActionFactory:
    """Builds the actions valid for one run, for one person, right now.

    Every method returns `None` when the action does not apply, so callers can
    build a list with `[a for a in (...) if a]` and never have to repeat the
    gating logic.
    """

    def __init__(self, context: ActionContext) -> None:
        """Initialize the ActionFactory.

        Args:
            context (ActionContext): The context.
        """
        self._ctx = context

    # ---- run control -----------------------------------------------------

    def pause(self) -> BusinessAction | None:
        """Pause the result.

        Returns:
            BusinessAction | None: The result.
        """
        if not self._ctx.can_act or self._ctx.run.run_status not in ("running", "resuming"):
            return None
        return BusinessAction(
            id="pause_run",
            type=BusinessActionType.PAUSE_RUN,
            label="Pause",
            description="Stop after the current step. Nothing already done is lost.",
        )

    def resume(self) -> BusinessAction | None:
        """Resume the result.

        Returns:
            BusinessAction | None: The result.
        """
        run = self._ctx.run
        paused_by_user = run.is_paused and (run.gate or {}).get("pause_kind") == "user_requested"
        if not self._ctx.can_act or not paused_by_user:
            return None
        return BusinessAction(
            id="resume_run",
            type=BusinessActionType.RESUME_RUN,
            label="Resume",
            description="Continue from where this was paused.",
            emphasis="primary",
        )

    def stop(self) -> BusinessAction | None:
        """Stop the result.

        Returns:
            BusinessAction | None: The result.
        """
        if not self._ctx.can_act or self._ctx.run.is_finished:
            return None
        return BusinessAction(
            id="stop_run",
            type=BusinessActionType.STOP_RUN,
            label="Stop",
            # Deliberately blunt: `delete_run` is the only stop primitive the
            # platform has, and dressing a deletion up as a soft pause is how
            # people lose work they meant to keep.
            description="Permanently stop this work item and delete its history.",
            emphasis="danger",
        )

    def recheck(self, *, label: str | None = None) -> BusinessAction | None:
        """Compute the recheck.

        Args:
            label (str | None): The label (optional, default None).

        Returns:
            BusinessAction | None: The result.
        """
        mode = self._ctx.rerun_mode
        if not self._ctx.can_act or mode is None:
            return None
        return BusinessAction(
            id="rerun_dependency",
            type=BusinessActionType.RERUN_DEPENDENCY,
            label=label or ("Retry safely" if mode == "retry" else "Recheck now"),
            description=(
                "Run the unfinished work again, reusing everything that already succeeded."
                if mode == "retry"
                else "Run these checks again from the current information."
            ),
            emphasis="primary",
            params={"mode": mode},
        )

    def approve(self) -> BusinessAction | None:
        """Compute the approve.

        Returns:
            BusinessAction | None: The result.
        """
        run = self._ctx.run
        if not self._ctx.can_act or not run.awaits_approval:
            return None
        if "approve" not in ((run.gate or {}).get("allowed_actions") or ["approve", "reject"]):
            return None
        return BusinessAction(
            id="approve",
            type=BusinessActionType.APPROVE,
            label="Review and respond",
            description="Open the review this work item is waiting on.",
            emphasis="primary",
            params={"node_id": (run.gate or {}).get("node_id")},
        )

    def assign(self, *, suggested: str | None = None) -> BusinessAction | None:
        """Compute the assign.

        Args:
            suggested (str | None): The suggested (optional, default None).

        Returns:
            BusinessAction | None: The result.
        """
        if not self._ctx.can_act:
            return None
        return BusinessAction(
            id="assign_work_item",
            type=BusinessActionType.ASSIGN_WORK_ITEM,
            label="Assign owner",
            description="Give this work item to a named person or team.",
            params={"suggested": suggested} if suggested else {},
        )

    # ---- working with the understanding ----------------------------------

    def edit_fact(
        self, field: str, *, label: str | None = None, emphasis: str = "secondary"
    ) -> BusinessAction | None:
        """Compute the edit fact.

        Args:
            field (str): The field.
            label (str | None): The label (optional, default None).
            emphasis (str): The emphasis (optional, default 'secondary').

        Returns:
            BusinessAction | None: The fact.
        """
        if not self._ctx.can_act:
            return None
        return BusinessAction(
            id=f"edit_fact:{field}",
            type=BusinessActionType.EDIT_FACT,
            label=label or "Enter manually",
            description="Correct or supply this value yourself.",
            emphasis=emphasis,  # type: ignore[arg-type]
            params={"field": field},
        )

    def explain(self, *, target: str = "decision") -> BusinessAction:
        """Compute the explain.

        Args:
            target (str): Target value (optional, default 'decision').

        Returns:
            BusinessAction: The result.
        """
        return BusinessAction(
            id=f"explain:{target}",
            type=BusinessActionType.EXPLAIN_DECISION,
            label="Why?",
            description="Show the facts and rules behind this.",
            params={"target": target},
        )

    def draft_clarification(self, *, topic: str, label: str = "Ask customer") -> BusinessAction | None:
        """Draft the clarification.

        Args:
            topic (str): The topic.
            label (str): The label (optional, default 'Ask customer').

        Returns:
            BusinessAction | None: The clarification.
        """
        if not self._ctx.can_act:
            return None
        return BusinessAction(
            id=f"draft_clarification:{topic}",
            type=BusinessActionType.DRAFT_CLARIFICATION,
            label=label,
            # The platform drafts; a person sends. External communication is a
            # deliberate human checkpoint (§55), not something a projection
            # screen should be able to trigger.
            description="Draft a question to the customer for you to review and send.",
            requires_approval=True,
            params={"topic": topic},
        )

    def add_note(self) -> BusinessAction | None:
        """Add the note.

        Returns:
            BusinessAction | None: The note.
        """
        if not self._ctx.can_act:
            return None
        return BusinessAction(
            id="add_note",
            type=BusinessActionType.ADD_NOTE,
            label="Add note",
            description="Record something about this work item for whoever picks it up.",
        )

    def route_override(self, *, current: str | None) -> BusinessAction | None:
        """Compute the route override.

        Args:
            current (str | None): Current value.

        Returns:
            BusinessAction | None: The override.
        """
        if not self._ctx.can_act:
            return None
        return BusinessAction(
            id="route_override",
            type=BusinessActionType.ROUTE_OVERRIDE,
            label="Change route",
            # Records a human handling decision on top of the automatic one. It
            # does not re-execute the graph — a person overriding where a case
            # goes is a business fact, not a workflow rerun, and conflating the
            # two would silently discard everything already done.
            description="Send this work item to a different team, recorded as your decision.",
            params={"current": current} if current else {},
        )

    # ---- looking things up -----------------------------------------------

    def review_attachment(self, *, file_key: str, name: str) -> BusinessAction:
        """Compute the review attachment.

        Args:
            file_key (str): The file key.
            name (str): Workflow or resource name.

        Returns:
            BusinessAction: The attachment.
        """
        return BusinessAction(
            id=f"document_review:{file_key}",
            type=BusinessActionType.DOCUMENT_REVIEW,
            label="Review datasheet" if "datasheet" in name.lower() else "Preview",
            description=f"Open {name}.",
            params={"file_key": file_key, "name": name},
        )

    def lookup_record(
        self, *, kind: str, reference: str, tool: str, server_id: str, argument: str
    ) -> BusinessAction | None:
        """Read a named record back out of the system of record.

        Only offered when the run's own workflow already used this MCP server
        and tool — so the button reaches a connection the platform is known to
        have, rather than one it hopes exists — and only to someone who may act
        on the work item: reading a customer's order out of the ERP is an
        outbound call, not passive viewing.
        """
        if not self._ctx.can_act:
            return None
        return BusinessAction(
            id=f"related_record_lookup:{kind}:{reference}",
            type=BusinessActionType.RELATED_RECORD_LOOKUP,
            label=f"Get {kind} details",
            description=f"Read {reference} from the system of record.",
            params={
                "reference": reference,
                "tool": tool,
                "server_id": server_id,
                "argument": argument,
                "kind": kind,
            },
        )

    def open_record(self, *, kind: str, reference: str) -> BusinessAction:
        """Compute the open record.

        Args:
            kind (str): The kind.
            reference (str): The reference.

        Returns:
            BusinessAction: The record.
        """
        return BusinessAction(
            id=f"open_related_record:{kind}:{reference}",
            type=BusinessActionType.OPEN_RELATED_RECORD,
            label=f"Open {reference}",
            params={"kind": kind, "reference": reference},
        )

    def technical_details(self, activity_id: str, *, label: str = "Technical details") -> BusinessAction:
        """Compute the technical details.

        Args:
            activity_id (str): The activity id.
            label (str): The label (optional, default 'Technical details').

        Returns:
            BusinessAction: The details.
        """
        return BusinessAction(
            id=f"open_technical_details:{activity_id}",
            type=BusinessActionType.OPEN_TECHNICAL_DETAILS,
            label=label,
            description="Nodes, model calls, rules and raw output behind this activity.",
            params={"activity_id": activity_id},
        )

    def ask_ai(self, *, question: str, label: str | None = None) -> BusinessAction:
        """Compute the ask ai.

        Args:
            question (str): Question text.
            label (str | None): The label (optional, default None).

        Returns:
            BusinessAction: The ai.
        """
        return BusinessAction(
            id=f"ask_ai:{abs(hash(question)) % 10_000_000}",
            type=BusinessActionType.ASK_AI,
            label=label or "Ask AI",
            params={"question": question},
        )
