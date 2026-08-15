# Pump Customer Case Routing Workflow

> **Archived — describes the v1 graph.**
> This design was replaced by the department-first Level 3 workflow, which now
> occupies `workflows/pump_manufacturer_case_routing.yaml`. See
> [PUMP_ROUTING_LEVELS.md](PUMP_ROUTING_LEVELS.md) for the current design.
> The graph documented below lives on at
> `workflows/test_fixtures/pump_case_routing_v1.yaml`, kept for the platform
> behaviour its 105 tests cover (`tests/test_pump_case_routing_v1.py`).

## Business Logic, Decision Architecture and Modification Guide

**Source of truth:** [`workflows/pump_manufacturer_case_routing.yaml`](../../workflows/pump_manufacturer_case_routing.yaml)
— workflow name *Pump Manufacturer Multilingual Customer Case Routing*, version `1.1`,
use case `industrial_customer_operations`.

> **Filename note.** Some earlier specs refer to `pump_customer_case_routing.workflow.yaml`.
> No such file exists. The current file is `workflows/pump_manufacturer_case_routing.yaml`
> and everything below is parsed from it.

### Current shape

| | Count |
|---|---|
| Total nodes | **113** |
| `RouterAgent` (decision) | **35** — 22 in `rule` mode, 13 in `conditions` mode |
| `MCPToolAgent` (Dynamics 365 lookup) | **12** — 8 on the normal path, 4 on emergency paths |
| `HumanInLoopAgent` (review gate) | **3** |
| `DataTransformAgent` | **62** — 61 terminal outcomes + 1 pass-through (`customer_confirmed`) |
| `TransformAgent` (the only LLM call) | **1** — `understand_message` |
| Edges | **55** |
| Terminal routes (`exit`) | **64** — 61 outcome nodes + 3 human-review gates |

**Inputs:** `message` (required), `subject`, `sender_email`, `source_channel`.
**Entry:** `understand_message`.

---

## 1. The workflow in one picture

Actual order of evaluation, taken from the edge list:

```text
understand_message              ← the only LLM call in the workflow
        ↓
safety_router                   ← decided from the message ALONE
        ↓ (no safety)
stoppage_router                 ← decided from the message ALONE
        ↓ (no stoppage)
find_customer                   ← first Dynamics 365 call on the normal path
        ↓
customer_match_router           ← 0 / 1 / many matches
        ↓
customer_confirmed
        ↓
7 parallel Dynamics lookups     ← ownership, credit, quote, order,
        ↓                          fulfilment, availability, installed unit
confidence_router               ← extraction quality gate
        ↓
ownership_availability_router   ← was ownership readable at all?
        ↓
second_ask_router               ← does this message contain several asks?
        ↓
serial_identity_router          ← does the quoted serial belong to this customer?
        ↓
intent_router                   ← the business fan-out
        ↓
family-specific routers         ← RFQ / order / status / change / …
        ↓
named person, team queue, or human review
```

### Why the order matters

Everything above `find_customer` runs **without any dependency on Dynamics 365**.
Everything below it depends on the ERP being reachable.

Safety and production stoppage sit above that line deliberately. For those two
case classes a false negative — an emergency parked in a queue — costs far more
than a false positive. Placing them first means an emergency cannot be delayed by:

- a Dynamics outage,
- a customer name that matches two accounts,
- low extraction confidence,
- or any of the 30-plus routers below.

The remaining gates are ordered cheapest-signal-first: identity ambiguity is
resolved before ownership, ownership before intent, and intent last, because
every family-specific router below `intent_router` reads facts the gates above
have already validated.

---

## 2. AI understands. Business rules decide.

The workflow makes exactly **one** model call, in `understand_message`
(`TransformAgent`, `temperature: 0.0`). Its system prompt states its own limit:

> *"Interpret multilingual customer messages accurately, but do not decide who
> should own the case. Extract facts only."*

Every routing decision after that point is a deterministic rule.

### 2.1 What the LLM produces

`understand_message.output_schema` — 27 fields, grouped by what they are for:

**Message interpretation**

| Field | Type | Routing effect |
|---|---|---|
| `language` | str | none (display) |
| `english_summary` | str | none (display) |
| `confidence` | float | **yes** — `confidence_router`, threshold `< 0.8` |

**Business intent**

| Field | Type | Routing effect |
|---|---|---|
| `intent` | str | **yes** — `intent_router` (10 branches) |
| `secondary_intents` | list | **yes** — `second_ask_router` |
| `secondary_reference` | str | display on the parent case |
| `requested_action` | str | none (display) |

**Priority signals — evaluated before everything else**

| Field | Type | Routing effect |
|---|---|---|
| `has_safety_issue` | bool | **yes** — `safety_router`, highest precedence |
| `production_stoppage` | bool | **yes** — `stoppage_router`, second precedence |

**Case classification**

| Field | Type | Routing effect |
|---|---|---|
| `technical_complexity` | str | **yes** — `rfq_complexity_router` (standard/technical/complex) |
| `lifecycle_stage` | str | **yes** — `technical_lifecycle_router` (presales/aftersales) |
| `complaint_type` | str | **yes** — `complaint_router` (7 branches) |
| `requested_document_type` | str | **yes** — `document_router` (5 branches) |

**Reference extraction — used as lookup arguments, not as decisions**

| Field | Feeds |
|---|---|
| `customer_name` | `find_customer` |
| `quotation_reference` | `get_quote`, and `order_quote_match_router` |
| `customer_po_reference` | `get_quote` |
| `sales_order_reference` | `get_sales_order`, `get_fulfilment` |
| `pump_model` | `get_availability`, and `order_availability_router` |
| `serial_number` | `get_installed_unit` |
| `contact_name`, `requested_delivery_date`, `requested_quantity` | display only |

**Boolean assertions about the message**

| Field | Routing effect |
|---|---|
| `technical_deviation_requested` | **yes** — `order_technical_deviation_router`, `change_deviation_router` |
| `price_or_terms_changed` | **yes** — `change_commercial_router` |
| `order_reference_present` | **yes** — `status_reference_router` |
| `serial_number_present` | **yes** — `technical_serial_router`, `spare_parts_serial_router` |
| `missing_information` | display only |

### 2.2 What the LLM is structurally prevented from producing

The schema contains **no** field for `key_account`, `credit_hold`,
`warranty_active`, `account_owner_name`, `route`, `department`,
`po_matches_quote`, `belongs_to_customer`, `availability_status` or
`production_started`.

That absence is the guarantee. No wording — persuasive, insistent or
adversarial — can supply a fact the model is never asked for. A customer writing
*"our account manager confirmed we are a strategic key account"* changes nothing,
because `rfq_standard_key_account_router` reads
`find_customer.first.key_account` from Dynamics and there is no extraction field
that could compete with it.

---

## 3. Dynamics 365 as business source of truth

Eight distinct lookups. All are configured `fail_on_error: false`, so a failure
becomes a routable fact rather than a crashed run.

| Lookup | Business question | Consumed by | Empty result | Call failure |
|---|---|---|---|---|
| `find_customer` | Which customer is this, and is it a key account? | `customer_match_router`, `rfq_standard_key_account_router`, `order_customer_router` | `count: 0` → customer-not-found routes | `order_customer_system_unavailable` |
| `get_account_ownership` | Who is assignable today? | all 7 owner routers, `ownership_availability_router` | blank name → team queue | `routing_operations_queue` |
| `get_credit_status` | May this order proceed commercially? | `order_credit_router` | treated as no hold | `order_credit_unknown` |
| `get_quote` | Does the PO match a quote that belongs to this customer? | `order_quote_match_router` | `order_commercial_review` | `order_commercial_review` *(see gap G3)* |
| `get_sales_order` | Does the referenced order exist, and has production started? | `status_order_found_router`, `change_order_found_router`, `change_production_router` | order-not-found routes | `status_system_unavailable` / `change_system_unavailable` |
| `get_order_fulfilment_status` | Why is this order late? | `fulfilment_router` | `status_normal` | `status_fulfilment_unknown` |
| `get_inventory_availability` | Can we deliver this pump? | `order_availability_router` | defaults to feasible | routes to supply chain *(see gap G3)* |
| `get_installed_unit` | Whose unit is this serial, and is it in warranty? | `serial_identity_router`, `warranty_status_router` | no conflict raised | no conflict raised |

`get_sales_order` resolves by our own order number **or** by the customer's own
purchase-order number, because customers quote their own reference far more
often than ours.

### Lookup dependency

```text
understand_message
        │
        ├─ (emergency paths only)
        │   emergency_customer_lookup ─→ emergency_ownership_lookup ─→ route_safety
        │   stoppage_customer_lookup  ─→ stoppage_ownership_lookup  ─→ route_production_stoppage
        │
        └─ find_customer ─→ customer_match_router ─→ customer_confirmed
                                                          │
                    ┌──────────────┬──────────┬───────────┼───────────┬────────────┬──────────────┐
                    ▼              ▼          ▼           ▼           ▼            ▼              ▼
              get_ownership   get_credit  get_quote  get_sales_order  get_fulfilment  get_availability  get_installed_unit
                    └──────────────┴──────────┴───────────┴───────────┴────────────┴──────────────┘
                                                          ▼
                                                  confidence_router
```

The seven parallel lookups all run; a lookup whose argument is empty is skipped
and reports `status: skipped`, `found: false` — which is why "no serial quoted"
and "serial belongs to someone else" stay distinguishable.

---

## 4. Unknown is not no

**The single most important rule in this workflow.**

A lookup that fails returns `found: false` with empty data — the same shape as a
lookup that succeeded and found nothing. Reading only `found` therefore
manufactures business facts out of outages:

| If read naively | The business is told | Reality |
|---|---|---|
| `find_customer.found == false` | "New customer — start onboarding" | The CRM was unreachable |
| `credit_hold == false` | "Finance checked; customer is clear" | Nobody checked |
| `get_sales_order.found == false` | "Your order does not exist" | The order exists; we couldn't read it |
| `production_started == false` | "Not in production, change is easy" | It may already be built |
| fulfilment fallback | **"Your order is running normally"** | Nothing was read at all |

Six routers therefore test `status` **before** value. The status values that mean
*we asked and got no answer* are `error`, `denied`, `needs_approval`. `skipped`
is deliberately excluded — it means we never asked, which the surrounding
routers already handle as "not found" or "no reference given".

| Router | Unknown branch | Terminal route |
|---|---|---|
| `order_customer_router` | `CUSTOMER_SYSTEM_UNAVAILABLE` | `order_customer_system_unavailable` |
| `order_credit_router` | `CREDIT_UNKNOWN` | `order_credit_unknown` |
| `status_order_found_router` | `ORDER_SYSTEM_UNAVAILABLE` | `status_system_unavailable` |
| `fulfilment_router` | `FULFILMENT_UNKNOWN` | `status_fulfilment_unknown` |
| `change_order_found_router` | `ORDER_SYSTEM_UNAVAILABLE` | `change_system_unavailable` |
| `change_production_router` | `PRODUCTION_STATUS_UNKNOWN` | `change_production_status_unknown` |
| `ownership_availability_router` | `OWNERSHIP_UNAVAILABLE` | `routing_operations_queue` |

Every one of these outcome nodes ends with:

```text
Automatic commercial action: BLOCKED — this case was routed because a
business fact could not be established, not because it was established.
```

The guard does not overcorrect: a lookup that genuinely answered *"no such
customer"* still routes to `order_customer_not_found`, not to the unavailable
route.

---

## 5. Emergency decision path

### Precedence

```text
SAFETY  >  PRODUCTION STOPPAGE  >  everything else
```

`safety_router` is the first node after `understand_message`. Its `CONTINUE`
branch feeds `stoppage_router`, whose `CONTINUE` branch feeds `find_customer`.
A message reporting both a safety risk and a stopped line reaches `route_safety`
only — a leaking solvent line is a safety case that also stopped production, not
a production case.

### Enrichment that cannot block

```text
safety_router ──SAFETY──→ emergency_customer_lookup ──→ emergency_ownership_lookup ──→ route_safety
stoppage_router ──PRODUCTION_STOPPAGE──→ stoppage_customer_lookup ──→ stoppage_ownership_lookup ──→ route_production_stoppage
```

These four lookups exist purely to *decorate* an already-decided route. Both
carry:

```yaml
fail_on_error: false     # a failure must not end the run
timeout_seconds: 5       # and a hang must not delay it either
```

`fail_on_error: false` alone is not enough. It promises the run survives a
*failure*; it says nothing about a *hang*, and the connection default is 60
seconds. To the person waiting for an emergency assignment, a 60-second stall is
indistinguishable from an emergency route that depends on the ERP. The 5-second
bound closes that gap. Worst observed sequential path: under 15 seconds.

The emergency outcome states its own evidence honestly:

```text
Customer identity (enrichment only — not a routing input):
  Dynamics 365 lookup: error (MCP_TOOL_TIMEOUT)
  Resolved account:
  Matching accounts: 0
  Service owner:
  Commercial owner:

Automatic commercial action: BLOCKED pending human review.
```

**Emergency routing never reads:** customer identity, ownership, credit,
extraction confidence, or ERP availability. All four are reported; none decide.

---

## 6. Customer resolution

```text
find_customer ──→ customer_match_router
                      ├── count > 1  → customer_ambiguous   (human review)
                      └── otherwise  → customer_confirmed   → 7 lookups
```

`find_customer` matches on *contains*, so a partial name such as "BASF" can
return several legal entities. Taking the first match would silently bind the
case to whichever sorted first — and the selected account determines **ownership,
credit position, quotations, orders and installed-base records**. A wrong pick is
not a cosmetic error; it routes the case to the wrong person and validates it
against the wrong commercial history.

Four outcomes:

| Situation | Result |
|---|---|
| Exactly one match | Continue |
| Several matches | `customer_ambiguous` — pauses, shows every candidate and the match count |
| No match | Continue; family routers handle it (`order_customer_not_found`, queue fallbacks) |
| Lookup failed | `order_customer_system_unavailable` on the order path |

---

## 7. Named person vs team queue

Ownership has four fields per role, from `get_account_ownership`:

| Field | Meaning |
|---|---|
| `<role>_name` | the **assignable** name — empty unless the directory says active |
| `<role>_recorded_name` | what the CRM account actually says |
| `<role>_status` | `active` / `inactive` / `not_in_directory` / `unassigned` |
| `<role>_team` | the owning team, for queue fallback |

Roles: `account_owner`, `territory_sales_owner`, `sales_engineer`,
`application_specialist`, `service_owner`.

**Recorded owner ≠ assignable owner.** People move team, go on long-term leave
and leave the company faster than master data is corrected. Routing to a
departed salesperson's mailbox loses the case silently, which is worse than
never naming an owner.

| Status | Routing |
|---|---|
| `active` | named person |
| `inactive` | team queue, with the recorded name and team stated |
| `not_in_directory` | team queue — on the account but unverifiable, not assumed present |
| `unassigned` | team queue |
| lookup failed | `routing_operations_queue` |

The seven owner routers all test `<role>_name != ''`, so the assignable-name
rule applies uniformly without each router re-implementing it.

`ownership_availability_router` sits once on the main path, before
`second_ask_router`. Without it, an ownership outage would fall through to a
team queue and look **identical to a genuinely unassigned account** — the real
owner would never learn the case arrived.

---

## 8. One email can contain several business requests

```text
"Pump P-100 is leaking.
 Please send us the certificate.
 Please quote two replacement pumps."

intent             = COMPLAINT
secondary_intents  = [DOCUMENT_REQUEST, RFQ]
```

`second_ask_router` fires when `secondary_intents` is not empty, sending the case
to `route_dual_intent` **instead of** the single-intent ladder. That node emits
the outcome text plus a structured `work_items` object:

```text
parent_summary, primary_ask, additional_asks,
secondary_reference, customer,
commercial_owner, service_owner, sales_engineer
```

and names the owner of each kind of ask, so a human opens one child item per ask
and coordinates a single combined reply.

> **Limitation.** `work_items` are addressable data objects **inside one parent
> run**. They are not independent child workflow runs. See §14.

---

## 9. Main intent router

`intent_router` (rule mode, `OTHER` as default) fans out to ten destinations:

| Intent | Next router | Terminal departments |
|---|---|---|
| `RFQ` | `rfq_complexity_router` | Key Account Sales, Territory Sales, Inside Sales, Sales Engineering, Application Engineering |
| `NEW_ORDER` | `order_customer_router` | CRM Ops, Sales, Sales Engineering, Finance, Supply Chain, Order Management |
| `ORDER_STATUS` | `status_reference_router` | Customer Support, Supply Chain, Quality, Logistics |
| `ORDER_CHANGE` | `change_order_found_router` | Order Management, Sales, Sales Engineering, Production Planning |
| `TECHNICAL_SUPPORT` | `technical_lifecycle_router` | Sales Engineering, Service |
| `COMPLAINT` | `complaint_router` | Logistics, Quality, Service, Sales, Customer Support |
| `SPARE_PARTS` | `spare_parts_serial_router` | Service / Spare Parts |
| `DOCUMENT_REQUEST` | `document_router` | Engineering Documentation, Quality, Customer Support |
| `INVOICE` | `invoice_finance` | Finance / Accounts Receivable |
| `OTHER` *(default)* | `manual_other_case` | Human triage |

---

## 10. RFQ routing

```text
RFQ → rfq_complexity_router
        ├── complex   → rfq_complex_owner_router
        │                 ├── named specialist → rfq_named_application_specialist
        │                 └── none            → rfq_application_engineering_queue
        ├── technical → rfq_technical_owner_router
        │                 ├── named engineer  → rfq_named_sales_engineer
        │                 └── none            → rfq_sales_engineering_queue
        └── standard  → rfq_standard_key_account_router
                          ├── key account → rfq_kam_owner_router
                          │                   ├── named KAM → rfq_named_kam
                          │                   └── none      → rfq_kam_queue
                          └── otherwise   → rfq_territory_owner_router
                                              ├── named owner → rfq_named_territory_owner
                                              └── none        → rfq_inside_sales_queue
```

Note the two-step shape used throughout: **complexity decides the function**
(model-derived), then **ownership decides the individual** (ERP-derived). A
technical RFQ goes to Sales Engineering whether or not a named engineer exists;
only the last hop differs.

**Which router to edit for which business change**

| Change | Router |
|---|---|
| What counts as technical vs complex | `understand_message` prompt (definitions) + `rfq_complexity_router` (thresholds) |
| Which function owns technical RFQs | `rfq_complexity_router` branch target |
| Whether key accounts bypass territory sales | `rfq_standard_key_account_router` |
| Fallback when nobody is named | the four `*_owner_router` queue branches |

---

## 11. New order — customer PO is not an accepted order

```text
NEW_ORDER → order_customer_router
              ├── lookup failed  → order_customer_system_unavailable
              ├── not found      → order_customer_not_found
              └── ok → order_quote_match_router
                        ├── no quotation named    → order_missing_quote_reference
                        ├── PO mismatch OR quote  → order_commercial_review
                        │    belongs elsewhere
                        └── ok → order_technical_deviation_router
                                  ├── deviation → order_technical_review
                                  └── no → order_credit_router
                                            ├── lookup failed → order_credit_unknown
                                            ├── on hold       → order_credit_hold
                                            └── clear → order_availability_router
                                                          ├── no pump model → order_product_not_identified
                                                          ├── not feasible  → order_supply_chain_review
                                                          └── feasible      → order_management_ready
```

**Sales Order vs Order Confirmation.** A customer PO arriving by email is a
*request to transact*, not a transaction. Only `order_management_ready` states
that every check passed; the other nine outcomes stop short of that on purpose.

```text
Customer PO
    ↓  identity, quotation, technical, credit, availability checks
Sales Order            = internal transaction being processed
    ↓  human acceptance
Order Confirmation     = customer-facing commitment
```

The workflow never produces the confirmation. Accepting a PO commits price,
specification and delivery date simultaneously — each owned by a different
function, none of which the workflow may speak for.

Two distinctions worth keeping:

- **No quotation named** (`order_missing_quote_reference`) is a *missing fact*,
  not a pricing dispute. Collapsing it into commercial review made review the
  default outcome for most orders and hid the real mismatches.
- **No product identified** (`order_product_not_identified`) means availability
  was never checked. Reading the empty result as "feasible" would confirm stock
  nobody looked at.

---

## 12. Order status

```text
ORDER_STATUS → status_reference_router
                 ├── no reference → status_missing_reference
                 └── → status_order_found_router
                         ├── lookup failed → status_system_unavailable
                         ├── not found     → status_order_not_found
                         └── → fulfilment_router
                                 ├── lookup failed     → status_fulfilment_unknown
                                 ├── MATERIAL_SHORTAGE → status_material_shortage
                                 ├── PRODUCTION_DELAY  → status_production_delay
                                 ├── QUALITY_HOLD      → status_quality_hold
                                 ├── DISPATCHED        → status_dispatched
                                 └── default           → status_normal
```

| Outcome | Primary | Supporting |
|---|---|---|
| `status_material_shortage` | Supply Chain / Material Planning | account owner visibility |
| `status_production_delay` | Supply Chain / Production Planning | account owner visibility |
| `status_quality_hold` | **Quality** | Supply Chain / Order Management |
| `status_dispatched` | Customer Support / Logistics Coordination | — |
| `status_normal` | Customer Support | — |
| `status_fulfilment_unknown` | Customer Support / Supply Chain | — |

Not every blocked order belongs to Supply Chain. The ERP's own reason code
decides the accountable function — a quality hold is Quality's to release.

> **A failed fulfilment lookup must never fall through to `NORMAL_STATUS`.**
> That fallback previously turned an outage into a positive delivery claim made
> to the customer. `FULFILMENT_UNKNOWN` is evaluated first for exactly this reason.

---

## 13. Order change

```text
ORDER_CHANGE → change_order_found_router
                 ├── lookup failed → change_system_unavailable
                 ├── not found     → change_order_not_found
                 └── → change_production_router
                         ├── lookup failed      → change_production_status_unknown
                         ├── production started → change_production_started
                         └── → change_commercial_router
                                 ├── terms changed → change_deviation_router
                                 │                     ├── + deviation → change_commercial_and_engineering
                                 │                     └── terms only  → change_commercial
                                 └── neither       → change_order_admin
```

Whether production has started decides whether a change is a simple amendment or
a scrap-and-rebuild — which is why an unreadable production state gets its own
route rather than defaulting to "not started".

`change_commercial_and_engineering` is the workflow's dual-department case:
Sales Engineering confirms feasibility, Order Administration reprices. Applying
one without the other produces an order that either cannot be built or cannot be
invoiced.

---

## 14. Technical support

```text
TECHNICAL_SUPPORT → technical_lifecycle_router
                      ├── presales → technical_presales_owner_router
                      │                ├── named engineer → technical_named_sales_engineer
                      │                └── none          → technical_sales_engineering_queue
                      └── aftersales → technical_serial_router
                                         ├── no serial → technical_need_serial
                                         └── → technical_service_owner_router
                                                 ├── named owner → technical_named_service_owner
                                                 └── none        → technical_service_queue
```

Pre-sales technical questions are a *selection* problem (Sales Engineering);
after-sales ones are an *installed-base* problem (Service). Without a serial
number the installed base cannot be identified at all, so the workflow asks
rather than guessing which unit is meant.

---

## 15. Serial number identity check

`get_installed_unit` is scoped to the resolved customer. `serial_identity_router`
fires when the unit exists but `belongs_to_customer` is false, routing to
`installed_unit_identity_conflict` — before `intent_router`, because a serial
that is not this customer's invalidates every installed-base path equally.

When ownership does not match, the lookup returns **only** the serial and the
flag. No account name, no install site, no pump model. The outcome states this
explicitly:

```text
What is deliberately not shown:
Which customer does own this unit, where it is installed, and what it is.
```

Usually a transcription error, occasionally a second-hand unit, rarely something
worse. All three need a person, and none of them should be resolved by quoting
the record back to whoever asked.

---

## 16. Complaints

`complaint_router` routes on `complaint_type`, by **accountability** rather than
by who received the message:

| Type | Primary | Supporting |
|---|---|---|
| `transport_damage` | Supply Chain / Logistics | Quality + Customer Support |
| `delivery` | Supply Chain / Logistics | account owner visibility |
| `product_quality` | **Quality** | Service |
| `warranty` | → `warranty_status_router` | |
| `commercial` | account owner | CRM / Customer Support |
| `documentation` | Customer Support / Document Coordination | Quality or Engineering |
| default | CRM / Customer Support | account owner visibility |

"Crate arrived crushed" and "casing has a casting crack" are one sentence apart
and land on entirely different accountable functions — transport damage is
Logistics with a carrier claim, a casting crack is Quality with a root cause.

---

## 17. Warranty is evidence, not authority

```text
COMPLAINT / warranty → warranty_status_router
                         ├── unit found, owned, warranty_active false → complaint_warranty_lapsed
                         └── otherwise                                → complaint_warranty
```

`complaint_warranty_lapsed` presents the expiry date and routes to Service /
Warranty as a **commercial decision**. It explicitly does not decide:

```text
What this workflow has NOT decided:
Whether the claim is refused. An expiry date on file is evidence, not a
contractual ruling — the claim may still be met on goodwill, under a service
agreement, or because the fault predates expiry.
```

Note the router requires `belongs_to_customer is_true`: a lapsed warranty on
somebody else's unit is an identity problem, not a warranty problem, and is
already caught upstream.

---

## 18. Spare parts and documents

```text
SPARE_PARTS → spare_parts_serial_router
                ├── no serial → spare_parts_need_serial
                └── → spare_parts_owner_router
                        ├── named service owner → spare_parts_named_owner
                        └── none                → spare_parts_queue
```

```text
DOCUMENT_REQUEST → document_router
        GA_DRAWING  → Sales Engineering / Engineering Documentation
        CERTIFICATE → Quality / Certification
        MANUAL      → Customer Support (Service when installed-base clarification needed)
        DATASHEET   → Customer Support / Inside Sales
        default     → Customer Support / Document Coordination
```

Document types differ in who is *accountable for their content*: a certificate
carries a quality attestation, a GA drawing carries engineering intent, a
datasheet is published collateral.

```text
INVOICE → invoice_finance     Finance / Accounts Receivable, account owner visibility
```

Sales keeps visibility because the relationship is theirs; Finance owns the
receivable because the money is theirs.

---

## 19. When the workflow stops automating

| Gate | What is uncertain | Shown to the reviewer | Blocked |
|---|---|---|---|
| `customer_ambiguous` | Which legal entity this is | message, extracted name, match count, every candidate | all routing |
| `manual_low_confidence` | Whether the message was understood (`confidence < 0.8`) | message, extracted fields, all live Dynamics facts | all routing |
| `manual_other_case` | Which business process applies (`intent = OTHER`) | message, extracted fields, live Dynamics facts | all routing |
| `installed_unit_identity_conflict` | Whose equipment this is | serial and ownership flag **only** | automatic technical response |

The first three are `HumanInLoopAgent` gates that pause the run. The fourth is a
terminal route — the case is decided (it needs a person), it just cannot proceed
automatically.

---

## 20. Department ownership map

Derived from the terminal route nodes:

| Business situation | Primary owner | Supporting |
|---|---|---|
| Safety incident | Service Emergency queue | Quality + EHS / Safety |
| Production stoppage | Service / Field Service emergency queue | Sales (account owner visibility) |
| Standard RFQ, key account | Key Account Sales | Inside Sales for quotation admin |
| Standard RFQ, other | Regional / Territory Sales → Inside Sales | Customer Support |
| Technical RFQ | Sales Engineering | account owner |
| Complex RFQ | Application / Product Engineering | account owner |
| New order, ready | Order Management / Customer Support | account owner |
| New order, PO mismatch | account owner | Inside Sales / Order Management |
| New order, technical deviation | Sales Engineering | account owner |
| Credit hold / unknown | Finance / Credit Control | account owner |
| Delivery feasibility | Supply Chain / Planning | Order Management |
| Customer master review | Customer Support / CRM Operations | Finance |
| Material shortage | Supply Chain / Material Planning | account owner |
| Production delay | Supply Chain / Production Planning | account owner |
| Quality hold | Quality | Supply Chain / Order Management |
| Dispatched | Customer Support / Logistics Coordination | — |
| Order change after production | Order Management | Supply Chain + Production Planning |
| Order change, technical + commercial | Sales Engineering | Order Administration |
| Pre-sales technical | Sales Engineering | account owner |
| After-sales technical | Service | account owner visibility |
| Transport damage / delivery complaint | Supply Chain / Logistics | Quality + Customer Support |
| Product quality complaint | Quality | Service |
| Warranty | Service / Warranty | Quality |
| Spare parts | Service / Spare Parts | Customer Support |
| Certificate | Quality / Certification | — |
| GA drawing | Sales Engineering / Engineering Documentation | account owner |
| Invoice | Finance / Accounts Receivable | account owner visibility |
| Ownership unreadable | Routing Operations | — |

---

## 21. Router reference

All 35 routers. `rule` mode evaluates conditions in order, first match wins;
`conditions` mode evaluates cases in order with an explicit fallback.

### Gates — run before intent routing

| Router | Business question | Input | Branches | Fallback |
|---|---|---|---|---|
| `safety_router` | Credible safety risk? | `has_safety_issue` | SAFETY → emergency chain | CONTINUE |
| `stoppage_router` | Production stopped? | `production_stoppage` | PRODUCTION_STOPPAGE → stoppage chain | CONTINUE |
| `customer_match_router` | One customer or many? | `find_customer.count > 1` | AMBIGUOUS → human | CONTINUE |
| `confidence_router` | Was the message understood? | `confidence < 0.8` | MANUAL_REVIEW → human | CONTINUE |
| `ownership_availability_router` | Was ownership readable? | `get_ownership.status` | OWNERSHIP_UNAVAILABLE → Routing Operations | CONTINUE |
| `second_ask_router` | Several asks? | `secondary_intents` not empty | DUAL → parent case | CONTINUE |
| `serial_identity_router` | Is the serial this customer's? | `belongs_to_customer is_false` | IDENTITY_CONFLICT | CONTINUE |
| `intent_router` | Which business process? | `intent` | 9 named intents | OTHER → human |

### RFQ

| Router | Question | Input | Fallback |
|---|---|---|---|
| `rfq_complexity_router` | How hard is this? | `technical_complexity` | STANDARD |
| `rfq_standard_key_account_router` | Key account? | `find_customer.first.key_account` | TERRITORY |
| `rfq_kam_owner_router` | Named KAM? | `account_owner_name != ''` | KAM_QUEUE |
| `rfq_territory_owner_router` | Named territory owner? | `territory_sales_owner_name != ''` | INSIDE_SALES_QUEUE |
| `rfq_technical_owner_router` | Named engineer? | `sales_engineer_name != ''` | SALES_ENGINEERING_QUEUE |
| `rfq_complex_owner_router` | Named specialist? | `application_specialist_name != ''` | APPLICATION_ENGINEERING_QUEUE |

### New order

| Router | Question | Input | Fallback |
|---|---|---|---|
| `order_customer_router` | Known customer? | `find_customer.status`, `.found` | CONTINUE |
| `order_quote_match_router` | PO valid against a quote we own? | `quotation_reference`, `po_matches_quote`, `belongs_to_customer` | CONTINUE |
| `order_technical_deviation_router` | Spec differs? | `technical_deviation_requested` | CONTINUE |
| `order_credit_router` | May it proceed? | `get_credit.status`, `credit_hold` | CONTINUE |
| `order_availability_router` | Can we deliver? | `pump_model`, `availability_status` | ORDER_MANAGEMENT |

### Order status / change

| Router | Question | Input | Fallback |
|---|---|---|---|
| `status_reference_router` | Reference given? | `order_reference_present` | CONTINUE |
| `status_order_found_router` | Order exists? | `get_sales_order.status`, `.found` | CONTINUE |
| `fulfilment_router` | Why late? | `get_fulfilment.status`, `fulfilment_status` | NORMAL_STATUS |
| `change_order_found_router` | Order exists? | `get_sales_order.status`, `.found` | CONTINUE |
| `change_production_router` | Already building? | `get_sales_order.status`, `production_started` | CONTINUE |
| `change_commercial_router` | Terms changed? | `price_or_terms_changed` | ORDER_ADMIN_CHANGE |
| `change_deviation_router` | Spec changed too? | `technical_deviation_requested` | SINGLE |

### Support / complaint / parts / documents

| Router | Question | Input | Fallback |
|---|---|---|---|
| `technical_lifecycle_router` | Pre- or after-sales? | `lifecycle_stage` | AFTERSALES |
| `technical_presales_owner_router` | Named engineer? | `sales_engineer_name != ''` | SALES_ENGINEERING_QUEUE |
| `technical_serial_router` | Serial given? | `serial_number_present` | CONTINUE |
| `technical_service_owner_router` | Named service owner? | `service_owner_name != ''` | SERVICE_QUEUE |
| `complaint_router` | What kind of complaint? | `complaint_type` | OTHER_COMPLAINT |
| `warranty_status_router` | Still in cover? | `warranty_active`, `belongs_to_customer` | IN_WARRANTY |
| `spare_parts_serial_router` | Serial given? | `serial_number_present` | CONTINUE |
| `spare_parts_owner_router` | Named service owner? | `service_owner_name != ''` | SPARE_PARTS_QUEUE |
| `document_router` | Which document? | `requested_document_type` | OTHER_DOCUMENT |

**Every fallback routes toward a person or a team queue.** No fallback silently
completes a commercial action.

---

## 22. Decision nodes vs output nodes

The most common maintenance mistake is editing an outcome's text and expecting
routing to change. It will not.

| Category | Count | Nodes | Changing it changes… |
|---|---|---|---|
| **UNDERSTANDING** | 1 | `understand_message` | what is extracted, and therefore what every router can see |
| **LOOKUP** | 12 | `find_customer`, `get_*`, `emergency_*`, `stoppage_*` | which business facts exist |
| **DECISION** | 35 | every `*_router` | **where the case goes** |
| **HUMAN REVIEW** | 3 | `customer_ambiguous`, `manual_low_confidence`, `manual_other_case` | when automation stops |
| **PASS-THROUGH** | 1 | `customer_confirmed` | nothing (fan-out point) |
| **ROUTE / OUTCOME** | 61 | every terminal `DataTransformAgent` | **only what the recipient reads** |

`rfq_complexity_router` **decides**. `rfq_named_sales_engineer` **describes the
result of that decision**. To send technical RFQs elsewhere, change the router's
branch target — not the wording of the outcome node.

### Outcome anatomy

Terminal nodes are `format` operations producing a `text` field, generally:

```text
CASE: <type>                  what kind of case this is
Primary team / owner          who acts
Supporting team               who is involved
Reason                        why this route
Action                        what to do next
[Second team required]        dual-department cases
[What is unknown]             unknown/error routes
[Automatic … action: BLOCKED] where automation stops
Contact / Company (verified)  who wrote in, and who we matched them to
Company (as stated)           the name they used
```

Verified and stated company names are kept separate deliberately: when they
differ, whoever picks the case up can see it rather than having it silently
reconciled.

Seven queue outcomes also emit `customer_reply_draft` — a neutral, customer-safe
line that deliberately omits internal routing evidence such as *"the recorded
owner has left the company"*.

---

## 23. Modification guide

### 23.1 Add a new intent (e.g. `RETURN_REQUEST`)

1. `understand_message.prompt_template` — add the value to the `intent` allowed list.
2. `understand_message.output_schema` — only if new fields are needed; `intent` already exists.
3. `intent_router.config.rules` — add a rule **above** the `OTHER` default.
4. Add downstream router(s) if the intent needs sub-decisions.
5. Add terminal route node(s) as `DataTransformAgent` with a `text` format op.
6. Add the branch target to the `intent_router` edge's `branches` map.
7. Add every terminal node id to the top-level `exit` list.
8. Run preflight — an unreachable node or a branch with no edge destination fails there.

### 23.2 Add a new department (e.g. Field Service)

Usually **no new router**: add a terminal route node, point an existing router
branch at it, add it to `exit`. Only add a router if a new *decision* is needed.
If the department has a named owner in the CRM, extend the ownership contract
first (§23.6).

### 23.3 Change who owns an RFQ

To send technical RFQs to Application Engineering:

- change `rfq_complexity_router`'s `TECHNICAL` branch target from
  `rfq_technical_owner_router` to `rfq_complex_owner_router`; **or**
- change `rfq_technical_owner_router`'s branch targets to the application
  engineering outcomes.

Editing `rfq_named_sales_engineer`'s text changes nothing about routing.

### 23.4 Change safety routing — **high risk**

Current path: `understand_message → safety_router → stoppage_router → find_customer`.

Moving `safety_router` below `find_customer`, `customer_match_router`,
`confidence_router` or any lookup re-introduces the exact defect the ordering
exists to prevent: an emergency held behind an ERP outage, an ambiguous name or a
low confidence score.

If you must reorder, preserve all four invariants: safety before ERP, safety
before ambiguity, safety before confidence, safety above stoppage.

### 23.5 Add a new Dynamics fact

Follow the established pattern:

```text
MCP lookup (fail_on_error: false)
    ↓
router that tests `status` FIRST, then value
    ↓
three destinations: normal / unknown-or-error / not-found
```

A new lookup that only handles the happy path re-creates the "unknown is no"
class of bug. Define all three before wiring the branch.

### 23.6 Change an owner lookup

Three distinct places, often confused:

| To change | Edit |
|---|---|
| Which CRM field holds the owner, or how active status is determined | the `get_account_ownership` handler |
| Whether a named owner is used or a queue is preferred | the relevant `*_owner_router` |
| What the recipient reads | the terminal outcome node text |

### 23.7 Change emergency timeouts

`timeout_seconds: 5` on the four emergency lookups is a *workflow-level* choice.
Raising the connection default must not silently weaken it — the per-node value
is what protects the emergency path.

---

## 24. Workflow design invariants

1. Safety routing happens before any ERP-dependent routing.
2. Safety outranks production stoppage when both are detected.
3. ERP failure must never become a negative business fact.
4. Customer wording cannot establish a CRM-owned fact.
5. Department assignment comes from deterministic rules, never from free-form model output.
6. Low confidence triggers review — except on emergency paths, which have already been decided.
7. An inactive owner cannot receive a case.
8. Ownership lookup failure is different from unassigned ownership.
9. A cross-customer serial must not expose the other customer's information.
10. Warranty expiry must not automatically reject a claim.
11. A customer PO does not automatically equal an accepted order.
12. Sales Order and Order Confirmation remain separate concepts.
13. Multi-intent work items remain one parent case until child-run orchestration exists.
14. Every external business fact needs an explicit unknown/failure route.
15. Every router branch needs an edge destination and a terminal behaviour.

---

## 25. Known workflow gaps

**G1 — Conflicting references across message and attachments.**
The workflow reads message fields only. If the body says `SO-1045` and an
attached PO says `SO-1054`, nothing notices. The fix is not to ask the model
which looks right, but to collect references per source and resolve them against
the ERP as a first-class state (`MATCH` / `NOT_FOUND` / `CONFLICT` /
`SYSTEM_ERROR`), routing `CONFLICT` to Order Management. The platform supports
file extraction; it is not wired into this YAML.

**G2 — Duplicate and superseding messages.**
"Order 100 pumps" / "correction, make that 10" / "ignore my previous message"
arrive as three independent runs, none of which can see the others. Correlation
needs shared business-level state above the workflow; it cannot be solved by
adding routers.

**G3 — Two routers still fold lookup failure into a business verdict.**
`order_quote_match_router` and `order_availability_router` do not test
`status` the way the other six do. A failed `get_quote` reads as a PO mismatch
(`order_commercial_review`); a failed `get_availability` reads as not-feasible
(`order_supply_chain_review`). Both still route to a **human**, so no commercial
action is taken on a manufactured fact — but the case is *labelled* as a
commercial or supply problem rather than a system outage. Consistent with the
other six would mean adding an unknown branch to each.

**G4 — No `experience` metadata.**
This workflow declares no per-node `experience` block, while
`crm_aware_customer_triage.yaml` and `multilingual_customer_request_triage.yaml`
do. Business View therefore falls back to humanised node ids — a business user
sees *"Rfq Named Kam"* rather than curated stage language. Adding `experience`
to the terminal routes and gates is pure YAML and changes no routing.

**G5 — Work items are not child runs.**
`route_dual_intent` emits structured `work_items`; nothing spawns a run per ask.
A child-run node would first have to define failure semantics, cancellation,
retry ownership, cost attribution, HITL representation and permission
inheritance.

---

## 26. Worked examples

**1 — Standard Dutch RFQ, known key account**
```text
understand_message (intent=RFQ, complexity=standard, language=nl)
→ safety_router CONTINUE → stoppage_router CONTINUE
→ find_customer (1 match) → customer_match_router CONTINUE → customer_confirmed
→ 7 lookups → confidence_router CONTINUE → ownership_availability_router CONTINUE
→ second_ask_router CONTINUE → serial_identity_router CONTINUE
→ intent_router RFQ → rfq_complexity_router STANDARD
→ rfq_standard_key_account_router KEY_ACCOUNT → rfq_kam_owner_router NAMED_KAM
→ rfq_named_kam
```

**2 — German technical RFQ** — as above until `rfq_complexity_router TECHNICAL`
→ `rfq_technical_owner_router NAMED_SALES_ENGINEER` → `rfq_named_sales_engineer`.

**3 — Customer PO with quotation mismatch**
```text
intent_router NEW_ORDER → order_customer_router CONTINUE
→ order_quote_match_router COMMERCIAL_REVIEW   (po_matches_quote false)
→ order_commercial_review        — order creation NOT reached
```

**4 — Order delayed by a quality hold**
```text
intent_router ORDER_STATUS → status_reference_router CONTINUE
→ status_order_found_router CONTINUE → fulfilment_router QUALITY_HOLD
→ status_quality_hold            — Quality, not Supply Chain
```

**5 — Multi-intent: leaking pump + certificate + replacement RFQ**
```text
understand_message (intent=COMPLAINT, secondary_intents=[DOCUMENT_REQUEST, RFQ])
→ … → second_ask_router DUAL → route_dual_intent
   work_items: primary COMPLAINT, additional [DOCUMENT_REQUEST, RFQ]
   complaint/document/RFQ ladders are NOT entered
```

**6 — Safety, ERP down, confidence 0.41, ambiguous customer**
```text
understand_message (has_safety_issue=true, confidence=0.41, customer_name="BASF")
→ safety_router SAFETY
→ emergency_customer_lookup   (times out at 5s → status error)
→ emergency_ownership_lookup  (skipped — no account id)
→ route_safety
```
`customer_match_router`, `confidence_router` and every lookup gate are never
reached, because none of them sits between the message and the safety decision.

---

## 27. Quick-reference decision map

```text
MESSAGE
  │
  ├─ Safety? ──────────────────→ Service Emergency + Quality/EHS   (no ERP dependency)
  │
  ├─ Production stopped? ──────→ Service/Field Service + Sales      (no ERP dependency)
  │
  └─ Normal
       │
       ├─ Customer ambiguous ──→ customer_ambiguous          (human)
       ├─ Low confidence ──────→ manual_low_confidence       (human)
       ├─ Ownership unreadable → routing_operations_queue
       ├─ Several asks ────────→ route_dual_intent           (parent case)
       ├─ Serial not theirs ───→ installed_unit_identity_conflict
       │
       └─ Intent
            ├─ RFQ ──────────────→ complexity → ownership → person or queue
            ├─ NEW_ORDER ────────→ customer → quote → deviation → credit → availability
            ├─ ORDER_STATUS ─────→ reference → order → fulfilment reason
            ├─ ORDER_CHANGE ─────→ order → production state → commercial/technical
            ├─ TECHNICAL_SUPPORT → lifecycle → serial → Service or Sales Engineering
            ├─ COMPLAINT ────────→ type → accountable function (warranty → cover check)
            ├─ SPARE_PARTS ──────→ serial → Service owner or queue
            ├─ DOCUMENT_REQUEST ─→ document type → accountable function
            ├─ INVOICE ──────────→ Finance / Accounts Receivable
            └─ OTHER ────────────→ manual_other_case          (human)
```

---

## 28. Related documents

- [`docs/DYNAMICS_365_MCP.md`](../DYNAMICS_365_MCP.md) — how a business system is
  reached. Note it documents the **Dataverse CRM** connector (`app/mcp/dynamics/`);
  this workflow uses the **Finance & Operations** connector (`app/mcp/d365_finance/`).
- [`eval/pump_manufacturer_routing_cases.md`](../../eval/pump_manufacturer_routing_cases.md)
  — 31 golden routing cases and the fixture data behind them.
