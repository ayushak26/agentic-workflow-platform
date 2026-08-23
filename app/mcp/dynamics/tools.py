"""The Dynamics 365 CRM tool vocabulary.

Deliberately a *business* vocabulary, not a Web API wrapper. Each tool answers a
question someone in the business would ask ("is this company already a
customer?", "what have we sold them?"), with a narrow schema that shows exactly
what it reads and exactly what it may write. Tools return CRM facts only; the UI/workflow layer assembles business context and makes responsibility, department, role, and routing decisions.

Two things the reference implementation
(`srikanth-paladugula/mcp-dynamics365-server`) does that are not repeated here:

*   **`accountData: z.object({})`** — an unrestricted write surface. A model, or
    a workflow author, could set any column on the record including ownership and
    state. Every write tool here declares its allowed fields explicitly, so the
    permitted write surface is visible in the Builder and enforced at the server.
*   **`$filter=_customerid_value eq ${accountId}`** — a caller-supplied value
    interpolated straight into an OData query. Every identifier here is
    GUID-validated and every free-text term is OData-escaped before it reaches a
    query string (see `odata.py`).

Also added, because the reference has none: `$select` on every read (so a lookup
returns the fields the tool declares rather than every column on the entity),
`$top` bounds, and a declared `outputSchema` per tool so results arrive as typed
data instead of a JSON string.
"""
from __future__ import annotations

from typing import Any

#: Fields a create may set, per entity. Anything else is refused by the server
#: rather than silently dropped — a workflow that thinks it set `ownerid` and
#: did not is worse than one that was told it cannot.
LEAD_WRITE_FIELDS: dict[str, str] = {
    "subject": "string",
    "firstname": "string",
    "lastname": "string",
    "companyname": "string",
    "emailaddress1": "string",
    "telephone1": "string",
    "description": "string",
}

ACTIVITY_WRITE_FIELDS: dict[str, str] = {
    "subject": "string",
    "description": "string",
    "scheduledend": "string",
}

CASE_UPDATE_FIELDS: dict[str, str] = {
    "title": "string",
    "description": "string",
    "prioritycode": "integer",
}


def _string(description: str, **extra: Any) -> dict[str, Any]:
    """Internal helper for the string step.

    Args:
        description (str): The description.
        **extra (Any): The extra.

    Returns:
        dict[str, Any]: The result.
    """
    return {"type": "string", "description": description, **extra}


def _account_summary() -> dict[str, Any]:
    """Internal helper for the account summary step.

    Returns:
        dict[str, Any]: The summary.
    """
    return {
        "type": "object",
        "properties": {
            "account_id": _string("Dynamics account GUID."),
            "account_name": _string("Registered account name."),
            "customer_number": {
                "type": ["string", "null"],
                "description": "Account number, when the record has one.",
            },
            "industry": {"type": ["string", "null"], "description": "Industry label."},
            "city": {"type": ["string", "null"], "description": "Primary address city."},
            "country": {"type": ["string", "null"], "description": "Primary address country."},
            "telephone": {"type": ["string", "null"], "description": "Main telephone."},
            "website": {"type": ["string", "null"], "description": "Website URL."},
            "status": _string("active or inactive."),
            "customer_type": {
                "type": ["string", "null"],
                "description": "Business classification used during enquiry routing, e.g. existing or internal_demo.",
            },
            "key_account": {
                "type": ["boolean", "null"],
                "description": "Whether the account is managed as a key account.",
            },
            "account_owner": {
                "type": ["string", "null"],
                "description": "System user GUID of the account owner, when assigned.",
            },
        },
        "required": ["account_id", "account_name", "status"],
    }


def _contact_summary() -> dict[str, Any]:
    """Internal helper for the contact summary step.

    Returns:
        dict[str, Any]: The summary.
    """
    return {
        "type": "object",
        "properties": {
            "contact_id": _string("Dynamics contact GUID."),
            "full_name": _string("Contact's full name."),
            "email": {"type": ["string", "null"], "description": "Primary email."},
            "telephone": {"type": ["string", "null"], "description": "Primary telephone."},
            "job_title": {"type": ["string", "null"], "description": "Job title."},
            "account_id": {"type": ["string", "null"], "description": "Parent account GUID."},
        },
        "required": ["contact_id", "full_name"],
    }


def _opportunity_summary() -> dict[str, Any]:
    """Internal helper for the opportunity summary step.

    Returns:
        dict[str, Any]: The summary.
    """
    return {
        "type": "object",
        "properties": {
            "opportunity_id": _string("Dynamics opportunity GUID."),
            "name": _string("Opportunity name."),
            "estimated_value": {
                "type": ["number", "null"],
                "description": "Estimated revenue, in the record's currency.",
            },
            "estimated_close_date": {
                "type": ["string", "null"],
                "description": "Estimated close date, ISO 8601.",
            },
            "status": _string("open, won or lost."),
            "account_id": {"type": ["string", "null"], "description": "Owning account GUID."},
        },
        "required": ["opportunity_id", "name", "status"],
    }


def _product_summary() -> dict[str, Any]:
    """Internal helper for the product summary step.

    Returns:
        dict[str, Any]: The summary.
    """
    return {
        "type": "object",
        "properties": {
            "product_id": _string("Dynamics product GUID."),
            "name": _string("Product name."),
            "product_number": {"type": ["string", "null"], "description": "Product number."},
            "description": {"type": ["string", "null"], "description": "Product description."},
        },
        "required": ["product_id", "name"],
    }


def _order_summary() -> dict[str, Any]:
    """Internal helper for the order summary step.

    Returns:
        dict[str, Any]: The summary.
    """
    return {
        "type": "object",
        "properties": {
            "order_id": _string("Dynamics order GUID."),
            "order_number": _string("Order number."),
            "name": {"type": ["string", "null"], "description": "Order name."},
            "ordered_on": {"type": ["string", "null"], "description": "Order date, ISO 8601."},
            "status": {
                "type": ["string", "null"],
                "description": "Current business status of the sales order.",
            },
            "confirmed_date": {
                "type": ["string", "null"],
                "description": "Date/time the order was confirmed, ISO 8601.",
            },
            "total_amount": {"type": ["number", "null"], "description": "Order total."},
            "products": {
                "type": "array",
                "description": "Line items on the order.",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": _string("Product name."),
                        "product_number": {"type": ["string", "null"], "description": "Product number."},
                        "serial_number": {"type": ["string", "null"], "description": "Serial number, when recorded."},
                        "quantity": {"type": ["number", "null"], "description": "Quantity ordered."},
                    },
                    "required": ["name"],
                },
            },
        },
        "required": ["order_id", "order_number"],
    }


def _activity_summary() -> dict[str, Any]:
    """Internal helper for the activity summary step.

    Returns:
        dict[str, Any]: The summary.
    """
    return {
        "type": "object",
        "properties": {
            "activity_id": _string("Dynamics activity GUID."),
            "activity_type": _string("Activity entity type, e.g. task or phonecall."),
            "subject": _string("Activity subject."),
            "created_on": {"type": ["string", "null"], "description": "Creation timestamp, ISO 8601."},
            "status": {"type": ["string", "null"], "description": "Activity status."},
        },
        "required": ["activity_id", "subject"],
    }



def _owner_summary() -> dict[str, Any]:
    """Internal helper for the owner summary step.

    Returns:
        dict[str, Any]: The summary.
    """
    return {
        "type": "object",
        "properties": {
            "user_id": _string("Dynamics system user GUID."),
            "full_name": _string("Owner display name."),
            "role": {"type": ["string", "null"], "description": "Business role."},
            "department": {"type": ["string", "null"], "description": "Business department."},
        },
        "required": ["user_id", "full_name"],
    }


def _quote_summary() -> dict[str, Any]:
    """Internal helper for the quote summary step.

    Returns:
        dict[str, Any]: The summary.
    """
    return {
        "type": "object",
        "properties": {
            "quote_id": _string("Dynamics quote GUID."),
            "quote_number": _string("Customer-facing quotation number."),
            "name": {"type": ["string", "null"], "description": "Quotation name."},
            "status": {"type": ["string", "null"], "description": "Current quotation status."},
            "total_amount": {"type": ["number", "null"], "description": "Quotation total."},
            "account_id": {"type": ["string", "null"], "description": "Owning customer account GUID."},
        },
        "required": ["quote_id", "quote_number"],
    }


def _shipment_summary() -> dict[str, Any]:
    """Internal helper for the shipment summary step.

    Returns:
        dict[str, Any]: The summary.
    """
    return {
        "type": "object",
        "properties": {
            "shipment_id": _string("Shipment GUID."),
            "shipment_number": _string("Shipment reference."),
            "status": {"type": ["string", "null"], "description": "Current shipment status."},
            "shipped_date": {"type": ["string", "null"], "description": "Shipment dispatch date."},
            "delivered_date": {"type": ["string", "null"], "description": "Delivery date, when delivered."},
            "order_id": {"type": ["string", "null"], "description": "Related sales-order GUID."},
            "account_id": {"type": ["string", "null"], "description": "Related customer account GUID."},
        },
        "required": ["shipment_id", "shipment_number"],
    }


def _service_case_summary() -> dict[str, Any]:
    """Internal helper for the service case summary step.

    Returns:
        dict[str, Any]: The case summary.
    """
    return {
        "type": "object",
        "properties": {
            "case_id": _string("Service-case GUID."),
            "case_number": _string("Customer-facing service-case number."),
            "title": {"type": ["string", "null"], "description": "Service-case title."},
            "status": {"type": ["string", "null"], "description": "Current service-case status."},
            "priority": {"type": ["string", "null"], "description": "Service-case priority."},
            "serial_number": {"type": ["string", "null"], "description": "Related equipment serial number."},
            "account_id": {"type": ["string", "null"], "description": "Related customer account GUID."},
            "current_owner": {
                "anyOf": [_owner_summary(), {"type": "null"}],
                "description": "Current owner of the case, resolved to business role and department when possible.",
            },
        },
        "required": ["case_id", "case_number"],
    }


def _collection(item_schema: dict[str, Any], key: str) -> dict[str, Any]:
    """Every collection-returning tool shares this envelope.

    `count` and `truncated` are part of the contract on purpose: a workflow rule
    asking "does this customer already have an open opportunity?" should read a
    number, and an author must be able to see when a result was cut off by the
    row limit rather than being genuinely empty.
    """
    return {
        "type": "object",
        "properties": {
            key: {"type": "array", "items": item_schema},
            "count": {"type": "integer", "description": "Number of records returned."},
            "truncated": {
                "type": "boolean",
                "description": "True when more records exist than the row limit returned.",
            },
        },
        "required": [key, "count", "truncated"],
    }


#: The tool catalogue. Each entry is (name, description, inputSchema,
#: outputSchema, annotations, typical_uses).
#:
#: `annotations` are declared honestly, but the Eurskem policy layer never
#: relies on them for a safety decision — see app/mcp/policy.py.
TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "get_current_user",
        "title": "Get Current User",
        "description": (
            "Return the identity the CRM connection is acting as. Use it to "
            "confirm a connection works and to see which user or application "
            "account writes will be attributed to."
        ),
        "operation": "read",
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "output_schema": {
            "type": "object",
            "properties": {
                "user_id": _string("Dynamics system user GUID."),
                "full_name": _string("Display name of the identity."),
                "business_unit_id": {"type": ["string", "null"], "description": "Business unit GUID."},
                "organization_id": {"type": ["string", "null"], "description": "Organization GUID."},
            },
            "required": ["user_id", "full_name"],
        },
        "typical_uses": [
            "Confirm the CRM connection is working before building on it",
            "See which identity CRM writes will be attributed to",
        ],
    },
    {
        "name": "find_account",
        "title": "Find Account",
        "description": (
            "Find CRM accounts whose name matches a company name. Use it to "
            "decide whether an incoming enquiry comes from an existing customer."
        ),
        "operation": "read",
        "input_schema": {
            "type": "object",
            "properties": {
                "company_name": _string(
                    "Company name to search for, as written by the customer. "
                    "Matched as a prefix and as a contains-match.",
                    minLength=2,
                    maxLength=200,
                ),
                "limit": {
                    "type": "integer",
                    "description": "Maximum accounts to return.",
                    "minimum": 1,
                    "maximum": 25,
                    "default": 5,
                },
            },
            "required": ["company_name"],
        },
        "output_schema": _collection(_account_summary(), "accounts"),
        "typical_uses": [
            "Decide whether an enquiry is from an existing customer",
            "Resolve a company name from an email signature to a CRM account",
            "Expose whether the company already exists as a CRM account",
        ],
    },
    {
        "name": "get_account",
        "title": "Get Account",
        "description": "Retrieve one CRM account by its identifier.",
        "operation": "read",
        "input_schema": {
            "type": "object",
            "properties": {
                "account_id": _string("Dynamics account GUID."),
            },
            "required": ["account_id"],
        },
        "output_schema": {
            "type": "object",
            "properties": {"account": _account_summary()},
            "required": ["account"],
        },
        "typical_uses": ["Read the full account record once you have its id"],
    },
    {
        "name": "find_contact",
        "title": "Find Contact",
        "description": (
            "Find CRM contacts by email address or name. Use it to identify the "
            "person who wrote to us."
        ),
        "operation": "read",
        "input_schema": {
            "type": "object",
            "properties": {
                "email": _string("Email address to match exactly.", maxLength=200),
                "name": _string("Full or partial contact name.", maxLength=200),
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 25,
                    "default": 5,
                    "description": "Maximum contacts to return.",
                },
            },
            "required": [],
        },
        "output_schema": _collection(_contact_summary(), "contacts"),
        "typical_uses": [
            "Identify the sender of an incoming email in the CRM",
            "Find the right person to reply to at a known account",
        ],
    },
    {
        "name": "get_contacts_for_account",
        "title": "Get Contacts For Account",
        "description": "List the contacts belonging to a CRM account.",
        "operation": "read",
        "input_schema": {
            "type": "object",
            "properties": {
                "account_id": _string("Dynamics account GUID."),
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "default": 20,
                    "description": "Maximum contacts to return.",
                },
            },
            "required": ["account_id"],
        },
        "output_schema": _collection(_contact_summary(), "contacts"),
        "typical_uses": ["Find who to contact at a customer account"],
    },
    {
        "name": "get_open_opportunities",
        "title": "Get Open Opportunities",
        "description": (
            "List open opportunities for a CRM account. Use it to see whether a "
            "commercial conversation is already under way before starting another."
        ),
        "operation": "read",
        "input_schema": {
            "type": "object",
            "properties": {
                "account_id": _string("Dynamics account GUID."),
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "default": 20,
                    "description": "Maximum opportunities to return.",
                },
            },
            "required": ["account_id"],
        },
        "output_schema": _collection(_opportunity_summary(), "opportunities"),
        "typical_uses": [
            "See whether the account already has an open opportunity",
            "Add commercial context to an incoming enquiry",
            "Avoid creating a duplicate opportunity",
        ],
    },
    {
        "name": "find_previous_orders",
        "title": "Find Previous Orders",
        "description": (
            "List past orders for a CRM account, including their line items and "
            "serial numbers. Use it to resolve vague references such as "
            "\"another pump like last time\" from recorded fact rather than by "
            "guessing."
        ),
        "operation": "read",
        "input_schema": {
            "type": "object",
            "properties": {
                "account_id": _string("Dynamics account GUID."),
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 25,
                    "default": 10,
                    "description": "Maximum orders to return, newest first.",
                },
                "serial_number": _string(
                    "Optional serial number to filter by. Returns only orders containing this serial number.",
                    maxLength=100
                ),
                "order_number": _string(
                    "Optional order number to filter by. Returns only orders containing this order number.",
                    maxLength=100
                ),
                "purchase_order_number": _string(
                    "Optional purchase order number to filter by. Returns only orders containing this purchase order number.",
                    maxLength=100
                ),
            },
            "required": ["account_id"],
        },
        "output_schema": _collection(_order_summary(), "orders"),
        "typical_uses": [
            "Resolve \"the same as last time\" to an actual product",
            "Find the serial number of equipment a customer already owns",
            "Check what a customer has bought before quoting",
        ],
    },
    {
        "name": "get_quotations_for_account",
        "title": "Get Quotations For Account",
        "description": (
            "List quotations belonging to a CRM account. Returns stored quotation "
            "facts only, including status and amount."
        ),
        "operation": "read",
        "input_schema": {
            "type": "object",
            "properties": {
                "account_id": _string("Dynamics account GUID."),
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "default": 20,
                    "description": "Maximum quotations to return.",
                },
            },
            "required": ["account_id"],
        },
        "output_schema": _collection(_quote_summary(), "quotations"),
        "typical_uses": [
            "Check whether a customer already has a quotation",
            "Read quotation status and amount for an account",
        ],
    },
    {
        "name": "find_quotation",
        "title": "Find Quotation",
        "description": (
            "Find a quotation by its customer-facing quotation number. "
            "Optionally scope the lookup to a known customer account."
        ),
        "operation": "read",
        "input_schema": {
            "type": "object",
            "properties": {
                "quote_number": _string("Customer-facing quotation number.", minLength=1, maxLength=100),
                "account_id": _string("Optional Dynamics account GUID used to scope the lookup."),
            },
            "required": ["quote_number"],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "quotation": {
                    "anyOf": [
                        _quote_summary(),
                        {"type": "null"},
                    ]
                },
                "found": {"type": "boolean"},
            },
            "required": ["quotation", "found"],
        },
        "typical_uses": [
            "Resolve a quotation number extracted from a customer enquiry",
            "Read the stored quotation status",
        ],
    },
    {
        "name": "find_order",
        "title": "Find Sales Order",
        "description": (
            "Find a sales order using an order number or an equipment serial number. "
            "Optionally scope the lookup to a known customer account."
        ),
        "operation": "read",
        "input_schema": {
            "type": "object",
            "properties": {
                "order_number": _string("Customer-facing sales-order number.", maxLength=100),
                "serial_number": _string("Equipment serial number recorded on an order line.", maxLength=100),
                "account_id": _string("Optional Dynamics account GUID used to scope the lookup."),
            },
            "required": [],
            "anyOf": [
                {"required": ["order_number"]},
                {"required": ["serial_number"]},
            ],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "order": {
                    "anyOf": [
                        _order_summary(),
                        {"type": "null"},
                    ]
                },
                "found": {"type": "boolean"},
            },
            "required": ["order", "found"],
        },
        "typical_uses": [
            "Resolve an order number extracted from a customer enquiry",
            "Find the order containing a referenced equipment serial number",
        ],
    },
    {
        "name": "get_shipments_for_account",
        "title": "Get Shipments For Account",
        "description": (
            "List shipment records belonging to a CRM account. Returns stored "
            "shipment facts only."
        ),
        "operation": "read",
        "input_schema": {
            "type": "object",
            "properties": {
                "account_id": _string("Dynamics account GUID."),
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "default": 20,
                    "description": "Maximum shipment records to return.",
                },
            },
            "required": ["account_id"],
        },
        "output_schema": _collection(_shipment_summary(), "shipments"),
        "typical_uses": [
            "Read shipment history for a customer",
            "Check stored shipment statuses",
        ],
    },
    {
        "name": "find_shipment",
        "title": "Find Shipment",
        "description": (
            "Find a shipment by shipment number or related sales-order identifier. "
            "Optionally scope the lookup to a known customer account."
        ),
        "operation": "read",
        "input_schema": {
            "type": "object",
            "properties": {
                "shipment_number": _string("Customer-facing shipment number.", maxLength=100),
                "order_id": _string("Related Dynamics sales-order GUID."),
                "account_id": _string("Optional Dynamics account GUID used to scope the lookup."),
            },
            "required": [],
            "anyOf": [
                {"required": ["shipment_number"]},
                {"required": ["order_id"]},
            ],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "shipment": {
                    "anyOf": [
                        _shipment_summary(),
                        {"type": "null"},
                    ]
                },
                "found": {"type": "boolean"},
            },
            "required": ["shipment", "found"],
        },
        "typical_uses": [
            "Resolve a shipment number extracted from a customer enquiry",
            "Find the shipment connected to a known sales order",
        ],
    },
    {
        "name": "get_service_cases_for_account",
        "title": "Get Service Cases For Account",
        "description": (
            "List service cases belonging to a CRM account. Returns stored case "
            "status, priority, equipment reference, and current owner identifier."
        ),
        "operation": "read",
        "input_schema": {
            "type": "object",
            "properties": {
                "account_id": _string("Dynamics account GUID."),
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "default": 20,
                    "description": "Maximum service cases to return.",
                },
            },
            "required": ["account_id"],
        },
        "output_schema": _collection(_service_case_summary(), "service_cases"),
        "typical_uses": [
            "See whether a customer already has a service case",
            "Read the existing case status and current owner identifier",
        ],
    },
    {
        "name": "find_service_case",
        "title": "Find Service Case",
        "description": (
            "Find a service case by case number or equipment serial number. "
            "Optionally scope the lookup to a known customer account."
        ),
        "operation": "read",
        "input_schema": {
            "type": "object",
            "properties": {
                "service_case_number": _string("Customer-facing service-case number.", maxLength=100),
                "serial_number": _string("Equipment serial number recorded on the case.", maxLength=100),
                "account_id": _string("Optional Dynamics account GUID used to scope the lookup."),
            },
            "required": [],
            "anyOf": [
                {"required": ["service_case_number"]},
                {"required": ["serial_number"]},
            ],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "service_case": {
                    "anyOf": [
                        _service_case_summary(),
                        {"type": "null"},
                    ]
                },
                "found": {"type": "boolean"},
            },
            "required": ["service_case", "found"],
        },
        "typical_uses": [
            "Resolve a service-case number extracted from an enquiry",
            "Find service history connected to an equipment serial number",
        ],
    },
    {
        "name": "find_product",
        "title": "Find Product",
        "description": "Find products in the CRM product catalogue by name or number.",
        "operation": "read",
        "input_schema": {
            "type": "object",
            "properties": {
                "search": _string("Product name or number.", minLength=2, maxLength=200),
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 25,
                    "default": 10,
                    "description": "Maximum products to return.",
                },
            },
            "required": ["search"],
        },
        "output_schema": _collection(_product_summary(), "products"),
        "typical_uses": [
            "Confirm a model designation the customer mentioned really exists",
            "Look up a product number before quoting",
        ],
    },
    {
        "name": "get_recent_activities",
        "title": "Get Recent Activities",
        "description": "List recent CRM activities for an account — calls, emails, tasks.",
        "operation": "read",
        "input_schema": {
            "type": "object",
            "properties": {
                "account_id": _string("Dynamics account GUID."),
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "default": 10,
                    "description": "Maximum activities to return, newest first.",
                },
            },
            "required": ["account_id"],
        },
        "output_schema": _collection(_activity_summary(), "activities"),
        "typical_uses": [
            "See whether we have already responded to this customer",
            "Add recent-history context before drafting a reply",
        ],
    },
    {
        "name": "create_lead",
        "title": "Create Lead",
        "description": (
            "Create a CRM lead. Changes data in Dynamics 365. Only the fields "
            "listed here can be set."
        ),
        "operation": "write",
        "input_schema": {
            "type": "object",
            "properties": {
                "subject": _string("Lead subject line.", minLength=1, maxLength=300),
                "company_name": _string("Company the lead is for.", maxLength=200),
                "first_name": _string("Contact first name.", maxLength=100),
                "last_name": _string("Contact last name.", maxLength=100),
                "email": _string("Contact email address.", maxLength=200),
                "telephone": _string("Contact telephone.", maxLength=50),
                "description": _string("What the lead is about.", maxLength=4000),
            },
            "required": ["subject"],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "lead_id": _string("GUID of the created lead."),
                "subject": _string("Subject as stored."),
                "created": {"type": "boolean", "description": "True when a record was created."},
            },
            "required": ["lead_id", "created"],
        },
        "typical_uses": ["Capture a new enquiry from an unknown company"],
    },
    {
        "name": "create_followup_activity",
        "title": "Create Follow-up Activity",
        "description": (
            "Create a follow-up task against a CRM account. Changes data in "
            "Dynamics 365."
        ),
        "operation": "write",
        "input_schema": {
            "type": "object",
            "properties": {
                "account_id": _string("Dynamics account GUID the task belongs to."),
                "subject": _string("Task subject.", minLength=1, maxLength=300),
                "description": _string("What needs doing.", maxLength=4000),
                "due_date": _string("Due date, ISO 8601 (YYYY-MM-DD).", maxLength=32),
            },
            "required": ["account_id", "subject"],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "activity_id": _string("GUID of the created task."),
                "subject": _string("Subject as stored."),
                "created": {"type": "boolean", "description": "True when a record was created."},
            },
            "required": ["activity_id", "created"],
        },
        "typical_uses": [
            "Record that a customer is waiting for a call back",
            "Leave a trace in the CRM after an automated triage",
        ],
    },
    {
        "name": "update_account_contact_details",
        "title": "Update Account Contact Details",
        "description": (
            "Update an account's contact details. Changes data in Dynamics 365. "
            "Only telephone, website and address fields can be changed — this "
            "tool cannot alter ownership, status, or commercial fields."
        ),
        "operation": "write",
        "input_schema": {
            "type": "object",
            "properties": {
                "account_id": _string("Dynamics account GUID."),
                "telephone": _string("Main telephone number.", maxLength=50),
                "website": _string("Website URL.", maxLength=200),
                "address_line1": _string("Street address.", maxLength=250),
                "address_city": _string("City.", maxLength=80),
                "address_country": _string("Country.", maxLength=80),
            },
            "required": ["account_id"],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "account_id": _string("GUID of the updated account."),
                "updated_fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Fields that were actually changed.",
                },
                "updated": {"type": "boolean", "description": "True when the record was changed."},
            },
            "required": ["account_id", "updated_fields", "updated"],
        },
        "typical_uses": [
            "Correct a phone number a customer supplied in an email",
        ],
    },
]

TOOLS_BY_NAME: dict[str, dict[str, Any]] = {
    definition["name"]: definition for definition in TOOL_DEFINITIONS
}

#: Read-only tool names, used to build a least-privilege connection (§25).
READ_ONLY_TOOLS: tuple[str, ...] = tuple(
    definition["name"]
    for definition in TOOL_DEFINITIONS
    if definition["operation"] == "read"
)

WRITE_TOOLS: tuple[str, ...] = tuple(
    definition["name"]
    for definition in TOOL_DEFINITIONS
    if definition["operation"] != "read"
)