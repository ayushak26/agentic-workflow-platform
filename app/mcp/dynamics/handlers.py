"""Tool implementations: business question in, typed record out.

Each handler maps one CRM capability onto Dataverse entities, and — crucially —
maps Dataverse's column names onto business field names. `_customerid_value`,
`statecode` and `industrycode` are Dataverse's vocabulary; a workflow author
should see `account_id`, `status` and `industry`.

That translation is the reason these handlers exist rather than the MCP server
returning raw entity records. It is also what makes the fixture backend a true
twin: both backends go through the same handlers, so the shape a workflow maps
against is produced in exactly one place.
"""
from __future__ import annotations

from typing import Any

from app.mcp.dynamics import odata
from app.mcp.dynamics.client import DynamicsBackend, DynamicsError

ACCOUNT_COLUMNS = [
    "accountid",
    "name",
    "accountnumber",
    "industrycode",
    "address1_city",
    "address1_country",
    "telephone1",
    "websiteurl",
    "statecode",
]

CONTACT_COLUMNS = [
    "contactid",
    "fullname",
    "emailaddress1",
    "telephone1",
    "jobtitle",
    "_parentcustomerid_value",
]

OPPORTUNITY_COLUMNS = [
    "opportunityid",
    "name",
    "estimatedvalue",
    "estimatedclosedate",
    "statecode",
    "_customerid_value",
]

ORDER_COLUMNS = [
    "salesorderid",
    "ordernumber",
    "name",
    "createdon",
    "totalamount",
    "_customerid_value",
    "products",
]

PRODUCT_COLUMNS = ["productid", "name", "productnumber", "description"]

ACTIVITY_COLUMNS = [
    "activityid",
    "activitytypecode",
    "subject",
    "createdon",
    "statecode",
    "_regardingobjectid_value",
]

#: Dataverse `statecode` for accounts/opportunities: 0 is the live state.
_ACTIVE = 0


def _account(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "account_id": row.get("accountid") or "",
        "account_name": row.get("name") or "",
        "customer_number": row.get("accountnumber"),
        "industry": row.get("industrycode"),
        "city": row.get("address1_city"),
        "country": row.get("address1_country"),
        "telephone": row.get("telephone1"),
        "website": row.get("websiteurl"),
        "status": "active" if row.get("statecode", _ACTIVE) == _ACTIVE else "inactive",
    }


def _contact(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "contact_id": row.get("contactid") or "",
        "full_name": row.get("fullname") or "",
        "email": row.get("emailaddress1"),
        "telephone": row.get("telephone1"),
        "job_title": row.get("jobtitle"),
        "account_id": row.get("_parentcustomerid_value"),
    }


def _opportunity(row: dict[str, Any]) -> dict[str, Any]:
    state = row.get("statecode", _ACTIVE)
    return {
        "opportunity_id": row.get("opportunityid") or "",
        "name": row.get("name") or "",
        "estimated_value": row.get("estimatedvalue"),
        "estimated_close_date": row.get("estimatedclosedate"),
        "status": {0: "open", 1: "won", 2: "lost"}.get(state, "open"),
        "account_id": row.get("_customerid_value"),
    }


def _order(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "order_id": row.get("salesorderid") or "",
        "order_number": row.get("ordernumber") or "",
        "name": row.get("name"),
        "ordered_on": row.get("createdon"),
        "total_amount": row.get("totalamount"),
        "products": [
            {
                "name": item.get("name") or "",
                "product_number": item.get("product_number"),
                "serial_number": item.get("serial_number"),
                "quantity": item.get("quantity"),
            }
            for item in (row.get("products") or [])
        ],
    }


def _product(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "product_id": row.get("productid") or "",
        "name": row.get("name") or "",
        "product_number": row.get("productnumber"),
        "description": row.get("description"),
    }


def _activity(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "activity_id": row.get("activityid") or "",
        "activity_type": row.get("activitytypecode") or "",
        "subject": row.get("subject") or "",
        "created_on": row.get("createdon"),
        "status": "open" if row.get("statecode", 0) == 0 else "completed",
    }


def _collection(
    rows: list[dict[str, Any]], key: str, limit: int
) -> dict[str, Any]:
    """Wrap results in the shared envelope.

    `truncated` is computed by asking for one row more than the limit and
    checking whether it came back — the only honest way to tell "there are
    exactly N" from "there are at least N", and the difference matters when a
    rule asks whether an account has any open opportunities.
    """
    truncated = len(rows) > limit
    return {key: rows[:limit], "count": min(len(rows), limit), "truncated": truncated}


def _limit(arguments: dict[str, Any], default: int, maximum: int) -> int:
    value = arguments.get("limit", default)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(parsed, maximum))


# --------------------------------------------------------------------------
# Read handlers
# --------------------------------------------------------------------------

async def get_current_user(
    backend: DynamicsBackend, arguments: dict[str, Any]
) -> dict[str, Any]:
    del arguments
    return await backend.whoami()


async def find_account(
    backend: DynamicsBackend, arguments: dict[str, Any]
) -> dict[str, Any]:
    company = arguments.get("company_name")
    limit = _limit(arguments, 5, 25)
    # Prefix and contains, OR-ed: a customer writing "ABC Chemicals GmbH"
    # should find "ABC Chemicals B.V." too, so a human can pick the right one.
    expression = odata.any_of(
        odata.string_filter("name", company),
        odata.contains_filter("name", company),
    )
    rows = await backend.query(
        "accounts",
        select=ACCOUNT_COLUMNS,
        filter_expression=expression,
        order_by="name asc",
        top=limit + 1,
    )
    return _collection([_account(row) for row in rows], "accounts", limit)


async def get_account(
    backend: DynamicsBackend, arguments: dict[str, Any]
) -> dict[str, Any]:
    account_id = odata.guid(arguments.get("account_id"), field="account_id")
    row = await backend.get("accounts", account_id, select=ACCOUNT_COLUMNS)
    if row is None:
        raise DynamicsError(
            f"No CRM account with id {account_id}.",
            code="CRM_RECORD_NOT_FOUND",
            retryable=False,
            suggested_action="Search by company name instead, or send the case to a person.",
        )
    return {"account": _account(row)}


async def find_contact(
    backend: DynamicsBackend, arguments: dict[str, Any]
) -> dict[str, Any]:
    email = (arguments.get("email") or "").strip()
    name = (arguments.get("name") or "").strip()
    if not email and not name:
        raise DynamicsError(
            "find_contact needs an email address or a name.",
            code="CRM_INVALID_ARGUMENTS",
            retryable=False,
            suggested_action="Map the sender's email from the extraction step.",
        )
    limit = _limit(arguments, 5, 25)
    filters = []
    if email:
        filters.append(odata.string_filter("emailaddress1", email))
    if name:
        filters.append(odata.contains_filter("fullname", name))
    rows = await backend.query(
        "contacts",
        select=CONTACT_COLUMNS,
        filter_expression=odata.any_of(*filters),
        order_by="fullname asc",
        top=limit + 1,
    )
    return _collection([_contact(row) for row in rows], "contacts", limit)


async def get_contacts_for_account(
    backend: DynamicsBackend, arguments: dict[str, Any]
) -> dict[str, Any]:
    limit = _limit(arguments, 20, 50)
    rows = await backend.query(
        "contacts",
        select=CONTACT_COLUMNS,
        filter_expression=odata.lookup_filter(
            "_parentcustomerid_value", arguments.get("account_id")
        ),
        order_by="fullname asc",
        top=limit + 1,
    )
    return _collection([_contact(row) for row in rows], "contacts", limit)


async def get_open_opportunities(
    backend: DynamicsBackend, arguments: dict[str, Any]
) -> dict[str, Any]:
    limit = _limit(arguments, 20, 50)
    rows = await backend.query(
        "opportunities",
        select=OPPORTUNITY_COLUMNS,
        filter_expression=odata.all_of(
            odata.lookup_filter("_customerid_value", arguments.get("account_id")),
            f"statecode eq {_ACTIVE}",
        ),
        order_by="estimatedclosedate asc",
        top=limit + 1,
    )
    return _collection(
        [_opportunity(row) for row in rows], "opportunities", limit
    )


async def find_previous_orders(
    backend: DynamicsBackend, arguments: dict[str, Any]
) -> dict[str, Any]:
    limit = _limit(arguments, 10, 25)
    rows = await backend.query(
        "salesorders",
        select=ORDER_COLUMNS,
        filter_expression=odata.lookup_filter(
            "_customerid_value", arguments.get("account_id")
        ),
        order_by="createdon desc",
        top=limit + 1,
    )
    return _collection([_order(row) for row in rows], "orders", limit)


async def find_product(
    backend: DynamicsBackend, arguments: dict[str, Any]
) -> dict[str, Any]:
    search = arguments.get("search")
    limit = _limit(arguments, 10, 25)
    rows = await backend.query(
        "products",
        select=PRODUCT_COLUMNS,
        filter_expression=odata.any_of(
            odata.contains_filter("name", search),
            odata.contains_filter("productnumber", search),
        ),
        order_by="name asc",
        top=limit + 1,
    )
    return _collection([_product(row) for row in rows], "products", limit)


async def get_recent_activities(
    backend: DynamicsBackend, arguments: dict[str, Any]
) -> dict[str, Any]:
    limit = _limit(arguments, 10, 50)
    rows = await backend.query(
        "activitypointers",
        select=ACTIVITY_COLUMNS,
        filter_expression=odata.lookup_filter(
            "_regardingobjectid_value", arguments.get("account_id")
        ),
        order_by="createdon desc",
        top=limit + 1,
    )
    return _collection([_activity(row) for row in rows], "activities", limit)


# --------------------------------------------------------------------------
# Write handlers
# --------------------------------------------------------------------------

def _text(arguments: dict[str, Any], key: str, limit: int) -> str | None:
    """Read an optional write field, bounded.

    Every write field is length-checked here as well as in the input schema:
    the schema is what the Builder and the model see, this is what the server
    enforces. A server that trusts its own advertised schema is a server that
    trusts its caller.
    """
    value = arguments.get(key)
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > limit:
        raise DynamicsError(
            f"{key} is too long ({len(text)} characters, maximum {limit}).",
            code="CRM_INVALID_ARGUMENTS",
            retryable=False,
        )
    return text


async def create_lead(
    backend: DynamicsBackend, arguments: dict[str, Any]
) -> dict[str, Any]:
    subject = _text(arguments, "subject", 300)
    if not subject:
        raise DynamicsError(
            "create_lead needs a subject.",
            code="CRM_INVALID_ARGUMENTS",
            retryable=False,
        )
    payload: dict[str, Any] = {"subject": subject}
    for argument, column, limit in (
        ("company_name", "companyname", 200),
        ("first_name", "firstname", 100),
        ("last_name", "lastname", 100),
        ("email", "emailaddress1", 200),
        ("telephone", "telephone1", 50),
        ("description", "description", 4000),
    ):
        value = _text(arguments, argument, limit)
        if value is not None:
            payload[column] = value

    lead_id = await backend.create("leads", payload)
    return {"lead_id": lead_id, "subject": subject, "created": True}


async def create_followup_activity(
    backend: DynamicsBackend, arguments: dict[str, Any]
) -> dict[str, Any]:
    account_id = odata.guid(arguments.get("account_id"), field="account_id")
    subject = _text(arguments, "subject", 300)
    if not subject:
        raise DynamicsError(
            "create_followup_activity needs a subject.",
            code="CRM_INVALID_ARGUMENTS",
            retryable=False,
        )
    payload: dict[str, Any] = {
        "subject": subject,
        # The Dataverse binding syntax for a polymorphic regarding lookup.
        "regardingobjectid_account@odata.bind": f"/accounts({account_id})",
    }
    description = _text(arguments, "description", 4000)
    if description:
        payload["description"] = description
    due_date = _text(arguments, "due_date", 32)
    if due_date:
        payload["scheduledend"] = due_date

    activity_id = await backend.create("tasks", payload)
    return {"activity_id": activity_id, "subject": subject, "created": True}


#: Exactly which columns this tool may touch. Narrow on purpose: the reference
#: implementation's `accountData: object` would let a caller set ownership,
#: state, or credit limits.
_ACCOUNT_CONTACT_FIELDS: dict[str, tuple[str, int]] = {
    "telephone": ("telephone1", 50),
    "website": ("websiteurl", 200),
    "address_line1": ("address1_line1", 250),
    "address_city": ("address1_city", 80),
    "address_country": ("address1_country", 80),
}


async def update_account_contact_details(
    backend: DynamicsBackend, arguments: dict[str, Any]
) -> dict[str, Any]:
    account_id = odata.guid(arguments.get("account_id"), field="account_id")

    unknown = sorted(
        set(arguments) - set(_ACCOUNT_CONTACT_FIELDS) - {"account_id"}
    )
    if unknown:
        # Refused, not ignored. A workflow that believes it set a field and
        # silently did not is worse than one that is told it cannot.
        raise DynamicsError(
            f"This tool cannot change {unknown}. It may only update: "
            f"{sorted(_ACCOUNT_CONTACT_FIELDS)}.",
            code="CRM_FIELD_NOT_WRITABLE",
            retryable=False,
            suggested_action="Use a tool that declares those fields, or make the change in Dynamics.",
        )

    payload: dict[str, Any] = {}
    changed: list[str] = []
    for argument, (column, limit) in _ACCOUNT_CONTACT_FIELDS.items():
        value = _text(arguments, argument, limit)
        if value is not None:
            payload[column] = value
            changed.append(argument)

    if not payload:
        return {"account_id": account_id, "updated_fields": [], "updated": False}

    await backend.update("accounts", account_id, payload)
    return {
        "account_id": account_id,
        "updated_fields": sorted(changed),
        "updated": True,
    }


HANDLERS = {
    "get_current_user": get_current_user,
    "find_account": find_account,
    "get_account": get_account,
    "find_contact": find_contact,
    "get_contacts_for_account": get_contacts_for_account,
    "get_open_opportunities": get_open_opportunities,
    "find_previous_orders": find_previous_orders,
    "find_product": find_product,
    "get_recent_activities": get_recent_activities,
    "create_lead": create_lead,
    "create_followup_activity": create_followup_activity,
    "update_account_contact_details": update_account_contact_details,
}
