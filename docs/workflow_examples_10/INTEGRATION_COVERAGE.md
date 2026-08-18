# Integration Coverage

Every integration below is a real, working connection in this codebase — verified by reading `app/mcp/*/tools.py` (the advertised tool catalog) against `app/mcp/*/handlers.py`'s `HANDLERS` dict (what's actually wired up), not assumed from the catalog alone. One connector (`dynamics365`) has real drift between the two — six advertised tools have no handler — and that drift is called out explicitly so it isn't repeated by accident later.

## The three connected business systems

| Connection id | Real system | Backing | Tool catalog / handler match |
|---|---|---|---|
| `dynamics365_finance_scm` | Finance & SCM (ERP) | MySQL, `d365f_*` tables (customers, quotes, salesorders, shipments, invoices, contracts, inventory, installedunits, products) | 12/12 advertised tools have real handlers — no drift |
| `dynamics365` | CRM | MySQL, `crm_*` tables (accounts, contacts, opportunities, quotations, salesorders, shipments, service_cases, contracts, installed_equipment, products, activitypointers) | **12/18 advertised tools have real handlers** — see drift note below |
| `business_records` | Lightweight cross-system MySQL layer | Same MySQL database, queried directly (not through the CRM/ERP connectors) | 10/10 advertised tools have real handlers — no drift |

### `dynamics365` catalog/handler drift — do not use these six

`find_quotation`, `get_quotations_for_account`, `get_shipments_for_account`, `find_shipment`, `get_service_cases_for_account`, `find_service_case` are declared in `app/mcp/dynamics/tools.py`'s `TOOL_DEFINITIONS` but have **no entry** in `app/mcp/dynamics/handlers.py`'s `HANDLERS` dict. Preflight cannot catch this — it only validates against the tool catalog, not the handler wiring — so a workflow calling one of these six would pass preflight and then fail at runtime with `MCP_UNKNOWN_TOOL` (not silently — `call_tool`'s dispatch returns a structured error, it doesn't crash). None of the 10 workflows in this portfolio call any of these six. Working tools on this connector: `get_current_user`, `find_account`, `get_account`, `find_contact`, `get_contacts_for_account`, `get_open_opportunities`, `find_previous_orders`, `find_product`, `get_recent_activities`, `create_lead`, `create_followup_activity`, `update_account_contact_details`.

### A real, deliberately-modeled id-space split

`dynamics365_finance_scm.find_customer` returns `account_id` in the **Finance & SCM** id space (`d365f_customers.customerid`). `dynamics365.find_account` returns `account_id` in a **separate CRM** id space (`crm_accounts.accountid`). `business_records.create_case`/`create_opportunity` write against the **CRM** id space specifically (`crm_service_cases.accountid`, joined via `crm_accounts`). These are not interchangeable — a workflow that resolves identity via Finance & SCM (for order/shipment/invoice lookups) and then needs to file a CRM case must do a **second**, CRM-side account lookup first. `w01sub_route_and_notify.yaml` does exactly this (`find_crm_account` before every `create_case` call); mixing the two up would silently file a case against the wrong id space.

## Integration-by-workflow

| Integration | Workflow | Tool(s) | Purpose |
|---|---|---|---|
| MCP → Finance & SCM | W01 (via `w01sub_route_and_notify`) | `find_account` (CRM, for case filing) | Resolve the CRM account a filed case belongs to |
| MCP → Finance & SCM | W01 (via `sp02_customer_identity_resolution`) | `find_customer`, `find_sales_order` | Resolve stated identity against real records, preserving both |
| MCP → CRM | W01 (via `w01sub_route_and_notify`) | `create_case` ×5 (one per department + general) | File a real, separate case per department an inquiry touches |
| MCP → Finance & SCM | W03 | `find_installed_unit` | Identify the exact equipment a service report concerns |
| MCP → Finance & SCM | W04 | `find_sales_order`, `find_shipment`, `find_order_fulfilment_status`, `find_inventory_availability` | Four independent, parallel order-status checks |
| MCP → Finance & SCM | W05 | `find_customer` | Check strategic/key-account status for approval routing |
| MCP → CRM | W05 | `find_account` | Resolve the CRM account an opportunity will be filed against |
| MCP → business_records (MySQL) | W05 | `create_opportunity` ×3 (auto / sales-mgr-approved / finance-approved) | File the approved quote as a real CRM opportunity |
| MCP → Finance & SCM | W07 | `find_invoice`, `find_sales_order`, `find_customer` | Verify an uploaded invoice against the system's own records |
| MCP → business_records (MySQL, read-only) | W09 | `query_readonly` (via `MCPAgent`, `allowed_tools` restricted) | Bounded, model-directed investigation of existing access — no write tools exposed |
| RAG (indexed knowledge collection) | W02, W03, W06 (via `sp03_internal_knowledge_answer` in W03) | — | Product specs, service manuals, procurement policy |
| Knowledge Retrieval (retrieval-only) | W02, W06 | — | Cheap "is there any internal documentation at all" signal, ahead of generation |
| Web Search (public, candidate-only) | W02, W10 | — | Optional company enrichment; public-context sanity check before drafting |
| Email | W01 (via `sp05_response_preparation`), W04, W08 | — | Customer acknowledgment; delivery-exception reply; manager onboarding notice |
| File upload + extraction | W07, W10 | — | Invoice document; call document |
| Subprocess (reusable) | W01, W03, W04, W09 → `sp01`/`sp02`/`sp03`; W01 → `sp04`/`sp05` (via `w01sub`); W08 → `w08sub_hr_setup`/`w08sub_it_account` | — | See `README.md`'s subprocess-reuse table |

## What was not, and could not honestly be, tested live

No MCP call, RAG query, or LLM step in any of these 18 files was actually executed against a live model or a live database connection in this session — everything above is **structural** verification: the tool exists in the catalog, has a real handler, and the workflow's config matches the tool's real input schema (confirmed by reading `input_schema`/the handler's `arguments.get(...)` calls directly, not by running it). `scripts/preflight_workflows.py` proves every workflow's graph, config, and template references are valid — it explicitly does not call an LLM or hit a live service. Treat "PASS" as "will compile and run without a structural error," not as "has been run end-to-end with real data."
