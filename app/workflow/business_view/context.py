"""Business context: the attachments and related records this work item touches.

Both feed two things — the right-hand context panel (§35) and, more usefully,
the resolution actions offered against missing information (§7, §66). "Pump
model missing" is only actionable as *Review datasheet* when a datasheet is
genuinely attached, and only as *Open SO 231706* when that order reference is
genuinely present.

The rule throughout: a record or file is listed when the run actually has it.
An attachment mentioned in the customer's prose but never uploaded is not an
attachment the platform can open, and offering a button for it would be a lie.
"""
from __future__ import annotations

from typing import Any

from app.workflow.business_view.actions import ActionFactory
from app.workflow.business_view.common import field_label, is_empty_value
from app.workflow.business_view.models import (
    SOURCE_LABELS,
    BusinessAttachment,
    BusinessRelatedRecord,
    BusinessSource,
)
from app.workflow.business_view.runstate import RunView

#: Extraction field → (record kind, the MCP tool that reads it, the argument
#: name that tool expects). Used only to offer a lookup when the run's own
#: workflow already calls that tool on that server.
RECORD_LOOKUPS: dict[str, tuple[str, str, str]] = {
    "sales_order_reference": ("order", "get_sales_order", "sales_order_reference"),
    "quotation_reference": ("quotation", "get_quote", "quotation_reference"),
    "serial_number": ("installed unit", "get_installed_unit", "serial_number"),
    "pump_model": ("product", "get_inventory_availability", "pump_model"),
    "customer_po_reference": ("purchase order", "get_quote", "customer_po_reference"),
    "secondary_reference": ("related order", "get_sales_order", "sales_order_reference"),
}

#: Human labels for the record kinds above.
RECORD_LABELS = {
    "order": "Sales order",
    "quotation": "Quotation",
    "installed unit": "Installed unit",
    "product": "Product",
    "purchase order": "Customer PO",
    "related order": "Related order",
}


def available_tools(run: RunView) -> dict[str, str]:
    """`tool name → server id` for every MCP tool this run's workflow declares.

    Read from the workflow spec rather than from a global registry: the point
    is "this process is already connected to that system", which is what makes
    a lookup button safe to offer.
    """
    tools: dict[str, str] = {}
    for node in run.nodes:
        if node.execution_kind != "external" or node.spec is None:
            continue
        config = node.spec.config or {}
        tool = config.get("tool")
        server = config.get("server_id")
        if isinstance(tool, str) and isinstance(server, str):
            tools.setdefault(tool, server)
    return tools


def _file_refs(value: Any) -> list[dict[str, Any]]:
    """Every `WorkflowFileRef`-shaped dict inside a run input value."""
    if isinstance(value, dict):
        if value.get("kind") == "workflow_file" and value.get("minio_key"):
            return [value]
        return []
    if isinstance(value, list):
        return [ref for item in value for ref in _file_refs(item)]
    return []


def build_attachments(run: RunView, factory: ActionFactory) -> list[BusinessAttachment]:
    """Real uploaded files on this run, each with a way to open it (§36)."""
    attachments: list[BusinessAttachment] = []
    for name, value in run.inputs.items():
        for ref in _file_refs(value):
            file_key = str(ref.get("minio_key"))
            display = str(ref.get("name") or name)
            attachments.append(
                BusinessAttachment(
                    id=str(ref.get("file_id") or file_key),
                    name=display,
                    kind=str(ref.get("category") or ref.get("extension") or "file"),
                    size_bytes=ref.get("size_bytes"),
                    file_key=file_key,
                    actions=[factory.review_attachment(file_key=file_key, name=display)],
                )
            )
    return attachments


def build_related_records(
    run: RunView, payload: dict[str, Any], factory: ActionFactory,
) -> list[BusinessRelatedRecord]:
    """Records in other systems this request names (§37)."""
    tools = available_tools(run)
    records: list[BusinessRelatedRecord] = []
    seen: set[str] = set()

    for field, (kind, tool, argument) in RECORD_LOOKUPS.items():
        reference = payload.get(field)
        if not isinstance(reference, str) or is_empty_value(reference):
            continue
        reference = reference.strip()
        if reference in seen:
            continue
        seen.add(reference)

        actions = [factory.open_record(kind=kind, reference=reference)]
        server_id = tools.get(tool)
        if server_id:
            lookup = factory.lookup_record(
                kind=kind, reference=reference, tool=tool,
                server_id=server_id, argument=argument,
            )
            if lookup is not None:
                actions.append(lookup)

        records.append(
            BusinessRelatedRecord(
                id=f"record:{field}",
                kind=kind,
                label=RECORD_LABELS.get(kind, field_label(field)),
                reference=reference,
                # The reference came from the customer's own message; the
                # record it points at is the system's. Attributing it to the
                # message is the accurate half.
                source=BusinessSource.MESSAGE,
                source_label=SOURCE_LABELS[BusinessSource.MESSAGE],
                actions=actions,
            )
        )

    return records


def customer_name(run: RunView, payload: dict[str, Any]) -> str | None:
    """The customer this work item is about, verified where possible.

    Prefers the account name a system of record returned over the name the
    customer typed — they differ more often than anyone expects ("BASF" vs
    "BASF SE"), and the verified one is the one a salesperson should act on.
    """
    for node in run.nodes:
        if node.execution_kind != "external" or not node.succeeded:
            continue
        first = node.output_dict().get("first")
        if isinstance(first, dict) and first.get("account_name"):
            return str(first["account_name"])
    stated = payload.get("customer_name")
    return str(stated) if isinstance(stated, str) and not is_empty_value(stated) else None
