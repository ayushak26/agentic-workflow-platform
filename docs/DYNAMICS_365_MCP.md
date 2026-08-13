# Dynamics 365 CRM Through MCP

How Eurskem AI reaches a business system, and why Dynamics is not part of the
workflow engine.

```
Visual Workflow Builder
        ↓
Generic MCP Tool                app/nodes/mcp_tool.py
        ↓
Eurskem MCP client + policy     app/mcp/{client,service,policy,registry}.py
        ↓
Dynamics 365 MCP Server         app/mcp/dynamics/server.py
        ↓
Dataverse Web API  ──or──  fixture store
        ↓
Microsoft Entra ID
```

The workflow says **"call this CRM capability."** It never says *"make this
Dynamics Web API HTTP request"*, and it never holds a credential.

The governing rule is unchanged from the Builder work:

| Change | Costs |
| --- | --- |
| New business process | Workflow configuration |
| New CRM capability | A new/updated MCP tool |
| New Dynamics node type | **Never** |

---

## 1. One node, any system

There is no `DynamicsGetAccountNode`. There is one `MCPToolAgent`, shown in the
palette as **MCP Tool**:

```
MCP Tool                          MCP Tool
  Server  Dynamics 365              Server  Dynamics 365
  Tool    Find Account              Tool    Get Open Opportunities
  Inputs  company_name ←            Inputs  account_id ←
          understand.company                find_account.first.account_id
```

Same node type. Different configuration. A test asserts that no node type in the
registry has `dynamics`, `crm`, `salesforce` or `dataverse` in its name.

Distinct from the pre-existing `MCPAgent`, which is an *autonomous loop* where a
model chooses which tools to call. That is a different capability with a
different risk profile — and precisely what should not be deciding whether to
write to a customer's CRM.

---

## 2. Servers are first-class connections

`app/mcp/registry.py` — `MCPServerConnection`:

```
id · display_name · description
transport · command · args
environment_secret_refs        {VAR_NAME: ENV_VAR_NAME}, never values
tool_allowlist · tool_denylist · write_policy · allowed_roles
tool_policies                  per-tool classification and approval overrides
timeout_seconds · max_result_bytes
is_mock · environment_label    a demo must never look like production
```

Workflow YAML contains only:

```yaml
server_id: dynamics365
tool: find_account
```

Credentials are referenced by **environment variable name**, resolved at
subprocess launch. A literal secret pasted where a reference belongs is rejected
by a validator — it would otherwise sit in whatever stores that configuration.
`describe()` powers the Builder's connection panel and returns *which* variables
are expected and whether each is set, never a value.

---

## 3. Discovery, not hardcoding

`GET /api/builder/mcp/servers/{id}/tools` asks the server. Each entry carries its
input schema (the Builder renders the form from it), its output schema (the
mapping picker reads it), its operation class, and its business description.

**A tool added to the MCP server appears in the Builder with no frontend
change.** That is the entire reason MCP is the extension mechanism.

```
Server  [ Dynamics 365 ▾ ]     Connected · Demo fixtures · 12 tools

Search tools  [ customer          ]

READ   Find Account              Decide whether an enquiry is from an existing customer
READ   Get Contacts For Account  Find who to contact at a customer account
READ   Get Open Opportunities    See whether the account already has an open opportunity
WRITE  Create Lead               Capture a new enquiry from an unknown company
```

---

## 4. Read vs write, and who decides

Classification precedence, strongest first (`app/mcp/policy.py`):

1. **Deployment policy** — the operator's stated fact about the tool.
2. **Server annotations** (`readOnlyHint`, `destructiveHint`) — believed only
   when they make a tool look *more* dangerous. A server claiming
   `readOnlyHint: true` on `delete_account` does not get to lower its own
   classification; the MCP specification says clients must not rely on these
   hints for security decisions.
3. **Name heuristics**, biased toward caution — a write verb anywhere beats a
   leading read verb, so `get_and_update_account` is a write.
4. **`unknown`**, treated as a write.

The gate every call passes through:

```
server allowed? → tool allowed? → role allowed? → read/write policy?
→ human approval satisfied? → execute
```

A language model is never the thing deciding whether a CRM write is acceptable.
It proposes; the deployment's policy disposes.

On the canvas:

```
Find Customer                 Update Customer
MCP Tool · dynamics365        MCP Tool · dynamics365
[External action] [READ]      [External action] [WRITE]
```

---

## 5. Human confirmation for writes

`write_policy: require_approval` is the default. A write runs only when a Human
Review step earlier on the run's path actually approved something — read from
completed node outputs, not asserted by the node's own config. A rejection never
counts.

An author may declare a write unattended (`allow_unattended_write: true`). That
waives the *approval*, never the connection's policy: a read-only connection
still refuses. The decision is visible on the canvas and warned about by
preflight, which is the requirement — not that it be forbidden.

---

## 6. The tool vocabulary

Business capabilities, not a Web API wrapper:

```
READ    get_current_user           find_account            get_account
        find_contact               get_contacts_for_account
        get_open_opportunities     find_previous_orders
        find_product               get_recent_activities

WRITE   create_lead                create_followup_activity
        update_account_contact_details
```

There is deliberately **no** `execute_dynamics_request(endpoint, method, body)`.
That single tool would destroy the safety boundary entirely, and a test asserts
its absence.

---

## 7. Hardening applied to the reference

`srikanth-paladugula/mcp-dynamics365-server` was used as an architectural
reference and inspected rather than copied. Four things it does are not repeated:

### OData injection

The reference builds:

```ts
`api/data/v9.2/opportunities?$filter=_customerid_value eq ${accountId}`
```

`accountId` arrives from a tool call — which, in an agentic system, means it can
arrive from a model reading a customer's email. Interpolated directly, a value
containing `or 1 eq 1` rewrites the query.

`app/mcp/dynamics/odata.py` applies two rules without exception: **identifiers
are GUID-validated** (not escaped — a non-GUID id is a caller error), and **free
text is escaped and bounded** (quotes doubled per OData literal rules, control
characters stripped, length capped).

```
lookup_filter("_customerid_value", "x' or 1 eq 1 or '")
  → ODataValueError: must be a Dynamics record id (a GUID)
```

### Unrestricted write surface

The reference's `create-account` takes `accountData: z.object({})` — any column,
including ownership and state. Every write tool here declares its allowed fields
explicitly, and an undeclared field is **refused, not ignored**:

```
update_account_contact_details may only update:
  telephone, website, address_line1, address_city, address_country
```

A workflow that believes it set `ownerid` and silently did not is worse than one
that is told it cannot.

### Unbounded reads

The reference applies no `$select`, `$top` or paging, so `fetch-accounts`
returns every column of every account. Every read here selects exactly the
declared columns, caps rows at the tool's documented maximum, and reports
`truncated` — computed by asking for one row more than the limit, which is the
only honest way to distinguish "exactly N" from "at least N".

### Text results

The reference serialises results into text. Every tool here declares an
`outputSchema` and returns `structuredContent`, so downstream mapping works on
typed fields. `app/mcp/results.py` still handles the text-JSON case for servers
that predate structured output — parsed safely, bounded by size and depth, and
never attempted on prose.

---

## 8. Authentication (§24)

**Decision: Entra ID client credentials — an application identity.**

Correct for this deployment because the platform performs unattended server-side
triage: there is no signed-in user at 03:00 when an email arrives, so there is no
delegated identity to act on behalf of.

**The consequence, stated plainly:** the application identity sees whatever its
security role grants, regardless of who triggered the run. Record-level
permissions of the person whose request caused the workflow are *not* applied. If
a deployment needs per-user CRM permissions honoured, this model is wrong for it
and delegated (on-behalf-of) auth is required — which in turn requires an
interactive session the triage use case does not have.

Least privilege therefore has to be enforced on the application user's security
role, not assumed from the caller.

---

## 9. Least privilege (§25)

Two ends, because either alone fails open under a configuration mistake:

| Layer | Mechanism |
| --- | --- |
| Platform | `tool_allowlist` — the platform will not *ask* |
| Dynamics | Application user's security role — Dynamics will not *allow* |

`read_only_dynamics_connection()` provides the read-only variant: allowlist
restricted to `READ_ONLY_TOOLS`, `write_policy: read_only`. Pair it with an
Entra application user holding a read-only security role.

---

## 10. Write safety (§30)

CRM writes reuse the same reserve-before-acting ledger as outbound email
(`app/integrations/operations.py`):

```
key = sha256(run_id:node_id, server:tool, arguments)

reserve(key) → completed?   → replay the recorded outcome, do not call again
             → in flight?   → refuse; a person reconciles
             → unseen       → record "in flight", call, record the outcome
```

* A retried run does not create a second lead.
* Two different runs performing the same action do not collide.
* A **definitive** failure (rejected field) frees the key, so a corrected retry
  works.
* An **ambiguous** failure (timeout after acceptance) keeps the reservation and
  refuses the retry with a message naming what to check. Where the outcome
  cannot be known, the platform stops rather than repeating an uncertain write.

---

## 11. Errors as business outcomes (§28)

```
CRM_RECORD_NOT_FOUND
  No CRM account with id …
  retryable: false
  → Search by company name instead, or send the case to a person.

DYNAMICS_FORBIDDEN
  Dynamics refused the request (403)
  → The application user's security role may not grant access to this entity.

MCP_INPUT_UNAVAILABLE
  Skipped: account_id had no value, so there was nothing to look up.
  → Normal when an earlier lookup found nothing. Add a rule for that case.
```

With `fail_on_error: false`, these become routable facts (`found: false`) rather
than a dead run.

---

## 12. Mock mode (§22, §23)

```
                   ┌─ DYNAMICS_MODE=live → Dataverse Web API → Entra ID
Dynamics MCP ──────┤
                   └─ DYNAMICS_MODE=mock → fixture store
```

Identical tool names, input schemas, output schemas, mappings and business
logic. **Only the connection changes.**

The fixture backend is not a stub that returns everything: it interprets
`$filter`, `$select`, `$orderby` and `$top`, and *fails loudly* on a filter shape
it cannot interpret. A backend that silently ignored filters would let a workflow
be built that breaks the moment it goes live.

`mock` is the default, so a fresh checkout demonstrates the full CRM flow with no
tenant. Every response carries `_mode`, and the Builder shows a **demo data**
badge — a green "Connected" badge over fixtures is how a demo becomes a lie.

Fixture set (`app/mcp/dynamics/fixtures.json`) includes ABC Chemicals GmbH
(ACC-1043) with a Dura 25 at serial **VF-29831**, an open *Pump Replacement 2026*
opportunity, and three orders — enough to demonstrate resolving "another pump
like last time" from record.

---

## 13. Configuration

```bash
DYNAMICS_MCP_ENABLED=true
DYNAMICS_MCP_MODE=mock          # or live

# live only — held by the deployment, never by a workflow
DYNAMICS_URL=https://your-org.crm.dynamics.com
DYNAMICS_TENANT_ID=...
DYNAMICS_CLIENT_ID=...
DYNAMICS_CLIENT_SECRET=...
```

Third-party MCP servers join the same table, with the same policy, timeout and
audit treatment:

```bash
MCP_SERVERS='[{"id":"erp","display_name":"Internal ERP","command":"node",
  "args":["/opt/mcp/erp/index.js"],"write_policy":"read_only",
  "environment_secret_refs":{"ERP_TOKEN":"ERP_API_TOKEN"}}]'
```

A configured server may not shadow a built-in id.

---

## 14. Audit (§27)

Every invocation records `run_id`, `node_id`, `server_id`, `tool_name`,
`operation_class`, `status`, timings — and for results, the field **names**
only. Never CRM record content: an audit collection full of customer names and
phone numbers is a second, less protected copy of the CRM. An audit write failure
is logged loudly and never fails the workflow.

---

## 15. Preflight

Zero-token **and zero-network** — a Builder check must not depend on a CRM being
reachable:

| Code | Catches |
| --- | --- |
| `MCP_SERVER_NOT_CONFIGURED` | A server id this deployment does not have |
| `MCP_TOOL_NOT_CONFIGURED` | No tool selected |
| `MCP_TOOL_NOT_ALLOWED` | A tool outside the connection's allowlist |
| `MCP_WRITE_NOT_PERMITTED` | A write against a read-only connection |
| `EXTERNAL_ACTION_WITHOUT_REVIEW` | A CRM write with no human review guaranteed |

Whether a tool actually *exists* is checked by the discovery panel, which can
reach the server.

---

## 16. The example workflow

`workflows/crm_aware_customer_triage.yaml` — zero CRM-specific node types:

```
Customer Email                    Input
      ↓
Understand Customer Request       AI Task      ← language, not business facts
      ↓
Find CRM Account                  MCP Tool · READ
      ↓
  ┌───┴────┐
Order      Open                   MCP Tool · READ  (parallel)
History    Opportunities
  └───┬────┘
      ↓
Assess Request                    Decision     ← deterministic policy
      ↓
Route Request
┌─────┬──────────┬────────────┬──────────────┐
Sales Support  Spare Parts   Human Review
```

Three scenarios, all verified end to end through the real MCP subprocess:

**A. Product stated explicitly** — German email naming a Dura 25, serial
VF-29831, production stopped. → Technical Support, urgency critical, CRM account
attached.

**B. "Another pump like the one we purchased last time"** — the interview case.
The AI is instructed **not** to guess and reports
`refers_to_previous_purchase: true` with `product_model` missing. The CRM returns
three orders spanning two products.

The honest outcome is **Human Review with the order history attached**, not a
silent pick. The newest order is a hose set, not the pump; taking its first line
item would look confident and be wrong. The workflow narrowed the question from
*unknowable* to *choose one of these three* — which is the value it adds.

**C. Company not in the CRM** — the lookup finds nothing, the context steps skip
cleanly (`MCP_INPUT_UNAVAILABLE`), and a rule routes to a person: *"this may be a
new prospect or a misspelling."*

A fourth rule catches the case worth naming: **several accounts matched**. "ABC
Chemicals GmbH" and "ABC Chemicals B.V." are different legal entities with
different contracts, so a person picks.

---

## 17. Platform changes this required

Two gaps surfaced while building the CRM workflow, both fixed in the engine
rather than worked around:

**List indexing in path resolution.** Integration results are list-shaped far
more often than node outputs are — a CRM search returns matches, not a record.
`{{outputs.find_account.data.accounts.0.account_id}}` now resolves, in both the
template engine and the rule engine. (The node also exposes `first`, which is the
readable form for the common case, alongside `count` so a workflow can notice
ambiguity instead of silently taking the first match.)

**Optional references.** A CRM lookup that found nothing has no account id, and
that is a business outcome, not a fault. `{{path?}}` resolves to None instead of
raising; the MCP node then reports `status: skipped`, `found: false` rather than
calling the server with null. Required remains the default — a template that
silently resolves to nothing is how a workflow emails a customer addressed to
"None".

---

## 18. Tests

| File | Covers |
| --- | --- |
| `tests/test_mcp_policy.py` (48) | Classification precedence, the gate, registry, secret handling |
| `tests/test_dynamics_mcp.py` (57) | OData injection, narrow writes, bounded reads, live client via mocked transport, fixture fidelity |
| `tests/test_mcp_tool_node.py` (42) | Discovery, result normalisation, write safety, idempotency, audit, genericness |
| `tests/test_builder_api.py` | Server list, tool discovery, tool test, credential leakage |
| `tests/test_logic_preflight.py` | MCP preflight codes, the shipped workflow clean |

---

## 19. Why this stays generic (§17)

The Builder primitive is **MCP Tool**. Today the server is Dynamics 365.
Tomorrow it can be SAP, Salesforce, ServiceNow, SharePoint, an internal ERP, a
pricing service — with the same authoring experience:

```
Select Server → Select Tool → Map Inputs → Inspect Output
```

A test asserts the node's config has exactly six fields, none of them
system-specific: a `dynamics_url` or `crm_entity` field here would mean the next
system needs a code change.

> **The workflow is the solution. Nodes and MCP tools are reusable vocabulary.**
