# Dynamics 365 Finance + Supply Chain MCP Server

A production-oriented Model Context Protocol server for Microsoft Dynamics 365 Finance and Supply Chain Management using the Finance & Operations OData `/data` endpoint.

## Why this server exists

Microsoft now provides a native Dynamics 365 ERP MCP server in supported Finance & Operations environments. Use Microsoft's native dynamic ERP MCP when your environment supports it and you want broad product-native coverage. This custom server is useful when you need:

- a controlled MCP surface for an interview/demo;
- strict read/write allowlists;
- deterministic structured OData filters;
- a stable adapter around custom public data entities;
- business-specific tools layered on top later.

This server is distinct from `app/mcp/dynamics/` in the main platform, which talks to Dynamics 365 CRM (Dataverse) — accounts, contacts, opportunities, orders. This server talks to Dynamics 365 Finance & Operations (F&O OData) — customers, sales orders, inventory, and other finance/SCM entities. Both are "Dynamics 365" but are different products with different APIs, and a deployment may configure either, both, or neither.

## Requirements

- Node.js 20+
- Dynamics 365 Finance / Supply Chain environment with OData access
- Microsoft Entra app registration
- Application registered inside Finance & Operations under **System administration > Setup > Microsoft Entra applications**, mapped to an appropriate service user
- Only the minimum roles/privileges needed by that service user

## Install

```bash
npm install
cp .env.example .env
```

Populate the required `FNO_*` settings in `.env`. Do not commit `.env`.

Then:

```bash
npm run check
npm test
npm run dev
```

To test with MCP Inspector:

```bash
npx @modelcontextprotocol/inspector npx tsx src/index.ts
```

## MCP tools

- `erp_health`
- `erp_list_entity_sets`
- `erp_describe_entity`
- `erp_query`
- `erp_get_record`
- `erp_create_record`
- `erp_update_record`
- `erp_delete_record`

### Recommended first calls

1. `erp_health {}`
2. `erp_list_entity_sets { "contains": "Customer" }`
3. `erp_list_entity_sets { "contains": "SalesOrder" }`
4. `erp_list_entity_sets { "contains": "Inventory" }`
5. `erp_describe_entity` on the entities you plan to use so you know the exact keys and properties.
6. Add confirmed aliases to `FNO_ENTITY_ALIASES_JSON`.

Example alias configuration:

```env
FNO_ENTITY_ALIASES_JSON={"customers":"CONFIRMED_CUSTOMER_ENTITY","salesOrders":"CONFIRMED_SALES_ORDER_ENTITY","inventory":"CONFIRMED_INVENTORY_ENTITY","openInvoices":"CONFIRMED_OPEN_INVOICE_ENTITY"}
```

The values above intentionally say `CONFIRMED_*`: exact public entity set names can vary by application version and customization. Discover them from `$metadata` rather than assuming them.

## Example read

```json
{
  "entity": "customers",
  "select": ["CustomerAccount", "Name", "dataAreaId"],
  "filter": [
    { "field": "CustomerAccount", "operator": "eq", "value": "C0001" },
    { "field": "dataAreaId", "operator": "eq", "value": "usmf" }
  ],
  "top": 10
}
```

## Example composite-key lookup

```json
{
  "entity": "customers",
  "key": {
    "dataAreaId": "usmf",
    "CustomerAccount": "C0001"
  }
}
```

Finance & Operations OData requires all fields of the entity key when addressing a record.

## Enabling writes safely

Writes are disabled by default. If you enable them, the server refuses to start unless `FNO_WRITE_ENTITY_ALLOWLIST` is non-empty.

For a narrow demo:

```env
FNO_ALLOW_WRITES=true
FNO_WRITE_ENTITY_ALLOWLIST=YourSafeDraftEntity,YourSafeCaseEntity
```

Deletes are also fail-closed: enabling deletes requires a non-empty `FNO_DELETE_ENTITY_ALLOWLIST`. Keep deletes disabled unless absolutely necessary:

```env
FNO_ALLOW_DELETES=false
```

If you must test delete in a sandbox:

```env
FNO_ALLOW_DELETES=true
FNO_DELETE_ENTITY_ALLOWLIST=YourDisposableSandboxEntity
```

## Authentication model

The server uses MSAL Node client credentials and requests the Finance & Operations environment resource scope:

```text
https://YOUR-ENVIRONMENT.operations.dynamics.com/.default
```

The Entra application must also be registered inside Finance & Operations and mapped to a service user. That service user's Finance & Operations security roles determine what the integration can do.

## Production notes

- Prefer a certificate or managed platform secret store over a long-lived client secret for production.
- Give the mapped service account minimum privileges.
- Configure read/write/delete entity allowlists.
- Keep high-risk financial posting, credit overrides, refunds, vendor bank changes, inventory adjustments, and destructive actions outside autonomous LLM control.
- Add human approval before tools that create financial commitments.
- The query tool supports Finance & Operations `cross-company=true` reads when explicitly requested.
- The server retries HTTP 429/502/503/504 with bounded backoff and honors `Retry-After`.
- OData next links are only followed when they stay on the configured Finance & Operations origin.
- stdout is reserved for MCP protocol traffic; operational messages go to stderr.

## Recommended business layer for your assessment

Keep these generic ERP tools as the low-level adapter, then add narrowly scoped tools such as:

```text
finance_get_credit_status
finance_get_open_invoices
scm_get_inventory_availability
scm_get_sales_order_status
scm_create_sales_order_draft
scm_get_purchase_order_status
```

Each business tool should call confirmed entity sets internally and expose only the minimum fields/actions the AI agent needs.
