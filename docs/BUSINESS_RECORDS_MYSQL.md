# Business Records MySQL — Data Reference

The `business_records` MySQL database (`app/mcp/business_records/`) is the one MCP
connector in this repo backed by a real, persistent database rather than an
in-process JSON fixture. It has **no code path to any real Dynamics system** —
writes only ever touch this local MySQL instance. It is seeded once from two
fixture files and then diverges from them as workflows read/write it.

- Schema: `app/mcp/business_records/schema.sql`
- Seed source: `app/mcp/d365_finance/fixtures.json` (→ `d365f_*` tables),
  `app/mcp/dynamics/fixtures.json` (→ `crm_*` tables)
- Seed script: `python -m app.mcp.business_records.seed`
- Connection: `docker compose exec mysql mysql -u eurskem-app -p business_records`
  (password `eurskem-local-dev` unless overridden via `BUSINESS_RECORDS_MYSQL_*` env vars)

## Tables stored

**`d365f_*` — Dynamics 365 Finance & Supply Chain (ERP)**

| Table | Primary key | Purpose |
|---|---|---|
| `d365f_customers` | `customerid` | Customer master record |
| `d365f_employees` | `employeeid` | Internal staff |
| `d365f_quotes` | `quoteid` | Sales quotes |
| `d365f_salesorders` | `salesorderid` | Sales orders |
| `d365f_shipments` | `shipmentid` | Shipments against an order |
| `d365f_invoices` | `invoiceid` | Invoices |
| `d365f_contracts` | `contractid` | Service/support contracts |
| `d365f_inventory` | `inventoryid` | Pump model stock/availability |
| `d365f_installedunits` | `installedunitid` | Equipment installed at a customer site |
| `d365f_products` | `productid` | Product catalog (specs as JSON) |

**`crm_*` — Dynamics 365 CRM (Dataverse-style)**

| Table | Primary key | Purpose |
|---|---|---|
| `crm_accounts` | `accountid` | Account/company master record |
| `crm_contacts` | `contactid` | People at an account |
| `crm_opportunities` | `opportunityid` | Sales opportunities |
| `crm_quotations` | `quoteid` | Quotes tied to an opportunity |
| `crm_salesorders` | `salesorderid` | Sales orders |
| `crm_salesorder_products` | `id` (auto-increment) | Line items on a sales order |
| `crm_shipments` | `shipmentid` | Shipments |
| `crm_service_cases` | `caseid` | Support/service cases |
| `crm_contracts` | `contractid` | Service contracts |
| `crm_installed_equipment` | `equipmentid` | Equipment installed at an account |
| `crm_products` | `productid` | Product catalog |
| `crm_activitypointers` | `activityid` | Activity log entries (calls, emails, tasks) |

Foreign keys point from child → parent (e.g. `d365f_quotes.customerid` →
`d365f_customers.customerid`, `crm_salesorders.accountid` →
`crm_accounts.accountid`). Respect that order for inserts/deletes below.

## What the MCP write tools cover — and what they don't

Only **3 tables** have MCP write support, via 5 tools total in
`app/mcp/business_records/handlers.py`. **There is no delete tool at all** —
for deletes, and for any table not listed below, you have to use direct SQL
(see the Delete section).

| Tool | Table | Operation |
|---|---|---|
| `create_case` | `crm_service_cases` | INSERT |
| `update_case` | `crm_service_cases` | UPDATE (`status`, `priority`) |
| `create_opportunity` | `crm_opportunities` | INSERT |
| `create_order` | `crm_salesorders` | INSERT |
| `update_order` | `crm_salesorders` | UPDATE (`status`, `total_amount`) |

All other tables (`d365f_*` entirely, and `crm_accounts`, `crm_contacts`,
`crm_quotations`, `crm_salesorder_products`, `crm_shipments`, `crm_contracts`,
`crm_installed_equipment`, `crm_products`, `crm_activitypointers`) can only be
modified via direct SQL against MySQL — there's no MCP tool for them.

`query_readonly` (also an MCP tool) can be used to read back any table —
it runs SELECT-only under a locked-down read-only credential (`sql_guard.py`).

## Adding data (INSERT)

**Via MCP tool** (only where a tool exists):

```json
// create_case
{"account_id": "account-1", "title": "Pump vibration issue", "priority": "high", "serial_number": "SN-1001"}

// create_opportunity
{"account_id": "account-1", "name": "Replacement pump upgrade", "estimated_value": 45000, "estimated_close_date": "2026-09-30"}

// create_order
{"account_id": "account-1", "order_number": "SO-2001", "purchase_order_number": "PO-5001", "name": "Q3 pump order", "total_amount": 12000}
```

IDs (`case_id`, `opportunity_id`, `order_id`) are auto-generated as
`prefix-N`, where N is a simple count of existing rows with that prefix —
fine for seeded demo data, not a real uniqueness guarantee under concurrent
writes.

**Via direct SQL** (any table, e.g. adding a new CRM account and a contact
under it — insert parent before child to satisfy the FK):

```sql
INSERT INTO crm_accounts (accountid, name, accountnumber, address1_country, statecode)
VALUES ('account-99', 'Acme Pumps Ltd', 'ACC-099', 'US', 0);

INSERT INTO crm_contacts (contactid, fullname, emailaddress1, accountid)
VALUES ('contact-99', 'Jane Doe', 'jane.doe@acme.example', 'account-99');
```

```sql
-- d365f example: new customer, then a sales order against it
INSERT INTO d365f_customers (customerid, name, sales_region, key_account, credit_hold)
VALUES ('cust-99', 'Acme Pumps Ltd', 'NA', FALSE, FALSE);

INSERT INTO d365f_salesorders (salesorderid, order_number, customerid, pump_model, order_status)
VALUES ('so-99', 'SO-9001', 'cust-99', 'DURA-25', 'open');
```

## Updating data (UPDATE)

**Via MCP tool** (only `crm_service_cases` and `crm_salesorders`):

```json
// update_case — looked up by service_case_number, not caseid
{"service_case_number": "CASE-0001", "status": "resolved", "priority": "low"}

// update_order — looked up by order_number, not salesorderid
{"order_number": "SO-2001", "status": "confirmed", "total_amount": 12500}
```

Both handlers only update the fields you pass (`status`/`priority` for
cases, `status`/`total_amount` for orders) and return the full updated row.

**Via direct SQL** (any table/column):

```sql
UPDATE d365f_customers SET credit_hold = TRUE WHERE customerid = 'cust-99';

UPDATE crm_opportunities SET estimatedvalue = 52000, statecode = 1
WHERE opportunityid = 'opp-1';

UPDATE d365f_inventory SET availability_status = 'BACKORDER', lead_time_days = 21
WHERE pump_model = 'DURA-25';
```

## Deleting data (DELETE)

**No MCP tool exists for delete, for any table.** All deletes must go
through direct SQL. Delete children before parents to satisfy foreign keys
(or delete the parent with `SET FOREIGN_KEY_CHECKS = 0` around it, but
prefer deleting in FK order — safer, and won't silently orphan rows):

```sql
-- Deleting a case (no FK children, safe on its own)
DELETE FROM crm_service_cases WHERE service_case_number = 'CASE-0001';

-- Deleting an account: delete dependents first
DELETE FROM crm_service_cases WHERE accountid = 'account-99';
DELETE FROM crm_opportunities WHERE accountid = 'account-99';
DELETE FROM crm_contacts WHERE accountid = 'account-99';
DELETE FROM crm_accounts WHERE accountid = 'account-99';

-- Deleting a sales order: line items reference the order, so go first
DELETE FROM crm_salesorder_products WHERE salesorderid = 'so-1';
DELETE FROM crm_salesorders WHERE salesorderid = 'so-1';
```

Run these via:

```bash
docker compose exec mysql mysql -u eurskem-app -p business_records
```

docker compose exec mysql mysql -u eurskem-app -p business_records \
  -e "SELECT * FROM crm_service_cases ORDER BY caseid DESC LIMIT 5;"

(full-privilege `eurskem-app` credential — needed for INSERT/UPDATE/DELETE;
the read-only credential used by `query_readonly` cannot write.)

## Verifying a change from code

- The MCP write tool's own return value already contains the row it just
  wrote (see examples above) — no extra query needed for the common case.
- To look something up later, use `query_readonly` with a parameterized
  `SELECT` against the table in question, e.g.:
  ```json
  {"sql": "SELECT * FROM crm_service_cases WHERE service_case_number = %(num)s", "params": {"num": "CASE-0001"}}
  ```
- Or query MySQL directly with the `mysql` CLI shown above.

## Note on `fixtures.json`

`fixtures.json` (in `app/mcp/d365_finance/` and `app/mcp/dynamics/`) is only
the original seed source — editing it does nothing to MySQL by itself.
Changes only reach the database if `python -m app.mcp.business_records.seed`
is re-run, and even then it's an upsert (won't delete rows you removed from
the JSON) and never runs automatically.
