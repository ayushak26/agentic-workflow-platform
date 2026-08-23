"""Executing a typed Business View action.

A Business View button never carries a prompt or a URL — it carries a
`BusinessActionType` and validated params, and lands here (§53). This module
is the only place that decides what each type actually does, so the set of
things a business screen can cause is enumerable by reading one file.

Three groups:

* **Handled here** — the capabilities the Business View adds: notes, route
  overrides, drafting a clarification, and reading a related record back out
  of a system of record.
* **Handled by a dedicated endpoint** — pause, resume, stop, retry/restart,
  approve/reject, assign, fact correction. Each already exists, is already
  audited, and is already tested; re-implementing them behind a second door
  would create two ways to do the same thing and only one of them audited.
  Sending one here is an explicit error naming the right route.
* **Client-side** — opening a document, a record view, or technical details.

Anything not in `BusinessActionType` at all is rejected before any work runs.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.observability.logging import get_logger
from app.workflow.business_view.models import BusinessActionType
from app.workflow.business_view.store import add_note, add_route_override

logger = get_logger(__name__)

CLARIFICATION_CAPABILITY = "clarification_drafting"
CLARIFICATION_MODEL = "gpt-5.6-luna"

#: Types with their own audited endpoint. Mapped to the route that owns them so
#: a misrouted call gets an answer rather than a shrug.
DELEGATED_ACTIONS: dict[BusinessActionType, str] = {
    BusinessActionType.PAUSE_RUN: "POST /api/runs/mine/{run_id}/pause",
    BusinessActionType.RESUME_RUN: "POST /api/runs/mine/{run_id}/resume",
    BusinessActionType.STOP_RUN: "DELETE /api/runs/mine/{run_id}",
    BusinessActionType.RERUN_DEPENDENCY: "POST /api/runs/mine/{run_id}/retry or /restart",
    BusinessActionType.APPROVE: "POST /api/runs/{run_id}/resume",
    BusinessActionType.REJECT: "POST /api/runs/{run_id}/resume",
    BusinessActionType.ASSIGN_WORK_ITEM: "POST /api/runs/mine/{run_id}/assign",
    BusinessActionType.EDIT_FACT: "POST /api/runs/mine/{run_id}/fact-correction",
}

#: Types the client performs without a round trip.
CLIENT_ACTIONS = {
    BusinessActionType.OPEN_TECHNICAL_DETAILS,
    BusinessActionType.OPEN_RELATED_RECORD,
    BusinessActionType.DOCUMENT_REVIEW,
    BusinessActionType.ASK_AI,
}


class BusinessActionError(Exception):
    """A typed action that cannot be performed as asked."""

    def __init__(self, message: str, *, status_code: int = 400):
        """Initialize the BusinessActionError.

        Args:
            message (str): Message text.
            status_code (int): The status code (optional, default 400).
        """
        super().__init__(message)
        self.status_code = status_code


class ClarificationDraft(BaseModel):
    """A question for the customer, drafted and never sent."""

    subject: str
    body: str
    asks: list[str] = Field(default_factory=list)


_CLARIFICATION_SYSTEM = (
    "You draft a short, polite clarification email from a pump manufacturer's "
    "sales desk to a business customer.\n"
    "Ask only for the listed missing information. Do not invent order numbers, "
    "products, prices, dates, names or commitments, and do not promise a lead "
    "time.\n"
    "Keep the body under 90 words. No signature block."
)


async def _draft_clarification(
    *, llm: Any, topic: str, context: dict[str, Any],
) -> dict[str, Any]:
    """Draft the clarification.

    Args:
        llm (Any): The llm.
        topic (str): The topic.
        context (dict[str, Any]): The context.

    Returns:
        dict[str, Any]: The clarification.
    """
    if llm is None:
        raise BusinessActionError("Drafting is unavailable right now.", status_code=503)
    payload = {
        "customer": context.get("customer"),
        "contact": context.get("contact"),
        "request": context.get("request"),
        "missing": context.get("missing") or [topic],
        "references": context.get("references") or [],
    }
    try:
        draft = await llm.complete_structured(
            model=CLARIFICATION_MODEL,
            system=_CLARIFICATION_SYSTEM,
            user=f"CONTEXT:\n{payload}",
            response_model=ClarificationDraft,
            temperature=0.2,
            max_tokens=400,
        )
    except Exception as exc:
        logger.warning("clarification_draft_failed", error=str(exc))
        raise BusinessActionError("Could not draft a question just now.", status_code=502) from exc
    return {
        "kind": "clarification_draft",
        "subject": draft.subject,
        "body": draft.body,
        "asks": draft.asks,
        # Said out loud in the payload as well as in the UI: nothing left the
        # building.
        "sent": False,
        "note": "Draft only — review and send it yourself.",
    }


async def _lookup_record(
    *, mcp: Any, params: dict[str, Any], run_id: str, session_id: str, role: str,
) -> dict[str, Any]:
    """Internal helper for the lookup record step.

    Args:
        mcp (Any): The mcp.
        params (dict[str, Any]): The params.
        run_id (str): Workflow run identifier.
        session_id (str): Session scope the record belongs to.
        role (str): User role.

    Returns:
        dict[str, Any]: The record.
    """
    tool = str(params.get("tool") or "")
    server_id = str(params.get("server_id") or "")
    argument = str(params.get("argument") or "")
    reference = str(params.get("reference") or "")
    if not (tool and server_id and argument and reference):
        raise BusinessActionError("This lookup is missing its target.")
    if mcp is None:
        raise BusinessActionError("The system of record is not connected.", status_code=503)

    try:
        result = await mcp.call(
            server_id=server_id,
            tool_name=tool,
            arguments={argument: reference},
            run_id=run_id,
            node_id="business_view_lookup",
            session_id=session_id,
            user_role=role,
            # A read never carries an approval; a tool classified as a write
            # will be refused by the policy gate, which is the correct outcome
            # for a button on a read-only business screen.
            approval_satisfied=False,
        )
    except Exception as exc:
        logger.info("business_record_lookup_failed", tool=tool, error=str(exc))
        raise BusinessActionError(
            f"{reference} could not be read from the system of record. Nothing was changed.",
            status_code=502,
        ) from exc

    data = result.get("data") or {}
    return {
        "kind": "record",
        "reference": reference,
        "record_kind": params.get("kind"),
        "data": data,
        "text": result.get("text"),
    }


async def dispatch_business_action(
    *,
    action_type: str,
    params: dict[str, Any],
    run_id: str,
    session_id: str,
    username: str,
    role: str,
    db: Any,
    services: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Perform one typed action and return what the UI should show.

    Raises `BusinessActionError` for anything invalid, unsupported, or owned by
    another endpoint.
    """
    try:
        typed = BusinessActionType(action_type)
    except ValueError as exc:
        raise BusinessActionError(f"'{action_type}' is not a Business View action.") from exc

    if typed in CLIENT_ACTIONS:
        raise BusinessActionError(f"'{typed.value}' is performed by the app, not the server.")
    if typed in DELEGATED_ACTIONS:
        raise BusinessActionError(
            f"'{typed.value}' is performed by {DELEGATED_ACTIONS[typed]}."
        )

    if typed is BusinessActionType.ADD_NOTE:
        text = str(params.get("text") or "").strip()
        if not text:
            raise BusinessActionError("A note needs some text.")
        record = await add_note(db, run_id=run_id, session_id=session_id, text=text, by=username)
        return {"kind": "note", "note": record}

    if typed is BusinessActionType.ROUTE_OVERRIDE:
        route = str(params.get("route") or "").strip()
        if not route:
            raise BusinessActionError("Choose the team this should go to.")
        record = await add_route_override(
            db,
            run_id=run_id,
            session_id=session_id,
            route=route,
            reason=params.get("reason"),
            by=username,
        )
        return {"kind": "route_override", "override": record}

    if typed is BusinessActionType.DRAFT_CLARIFICATION:
        llm = services.get("llm")
        if llm is not None and hasattr(llm, "with_context"):
            llm = llm.with_context(
                run_id=run_id,
                session_id=session_id,
                node_id=CLARIFICATION_CAPABILITY,
                ledger=services.get("cost_ledger"),
            )
        return await _draft_clarification(
            llm=llm, topic=str(params.get("topic") or ""), context=context or {},
        )

    if typed is BusinessActionType.RELATED_RECORD_LOOKUP:
        return await _lookup_record(
            mcp=services.get("mcp"),
            params=params,
            run_id=run_id,
            session_id=session_id,
            role=role,
        )

    if typed is BusinessActionType.EXPLAIN_DECISION:
        # Explanations are served by their own route, which already has the
        # decision in hand.
        raise BusinessActionError(
            "'explain_decision' is performed by GET /api/runs/mine/{run_id}/business-explanation."
        )

    raise BusinessActionError(f"'{typed.value}' has no handler.", status_code=501)
