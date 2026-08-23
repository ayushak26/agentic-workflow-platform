# Golden routing cases — pump manufacturer case routing

> **Archived — cases for the v1 graph**, now at
> `workflows/test_fixtures/pump_case_routing_v1.yaml`. The current routing cases
> are in [pump_routing_levels_cases.md](pump_routing_levels_cases.md).

Workflow: `workflows/pump_manufacturer_case_routing.yaml` (111 nodes, 63 exits).

## Routing priority

Emergencies are decided from the message alone, ahead of every ERP lookup and
every quality gate, because for this case class a false negative costs far more
than a false positive:

```
understand message
      ↓
safety risk?            → Service Emergency + QHSE      (no ERP dependency)
      ↓
production stopped?     → Service Emergency + Sales     (no ERP dependency)
      ↓
Dynamics 365 enrichment (find customer → ownership, credit, quote, order,
      ↓                  fulfilment, availability, installed unit)
customer name ambiguous? → pause for identity resolution
      ↓
extraction confidence low? → pause for manual review
      ↓
more than one ask?      → parent case with child work items
      ↓
serial belongs to someone else? → pause, disclose nothing
      ↓
intent routing
```

Enrichment on the two emergency paths runs with `fail_on_error: false`, so an
outage yields `status: error` and the case still reaches the emergency queue.
Identity is reported there as evidence, never as a routing input.

### Emergency enrichment budget

`fail_on_error` promises the run survives a *failure*, not a *hang*. The four
emergency lookups therefore declare their own bound, enforced by the
integration service rather than delegated to the MCP client:

```
Individual MCP call        ≤  5 s   (MCPToolConfig.timeout_seconds)
Total emergency enrichment ≤ 15 s   (asserted on the worst sequential path)
Connection default         =  60 s  (unchanged for every other call)
```

Routing proceeds with UNKNOWN evidence when the budget expires. A timeout is
distinguishable from an ordinary failure — `status: error` with
`error_code: MCP_TOOL_TIMEOUT` and `retryable: true` — so the outcome can say
"the ERP did not respond within the emergency time limit" rather than the
weaker "the ERP lookup failed". Timeouts also cancel the underlying operation
rather than abandoning it, and the next call succeeds normally.

Companion to `eval/sample_routing_cases.md`, which covers a **different** workflow
(`workflows/crm_aware_customer_triage.yaml`) against a **different** fixture set
(`app/mcp/dynamics/fixtures.json`, the Dataverse CRM connector). The two share no
schema fields and no customers — do not read either one as describing the other.

Paste `Subject` / `Message` / `Sender` into the Run dialog and open the run in
Cockpit to watch understanding → Dynamics 365 lookup → decision → routing.

## How far these are verified

Every routing outcome below is pinned by an automated test in
`tests/test_pump_manufacturer_case_routing.py`, which runs the **real** graph,
routers and MCP handlers against the fixtures listed here. Only the LLM
extraction step is stubbed, which means:

- the **routing decision** for a given set of extracted fields is verified;
- the **extraction** — that this specific wording produces those fields — is
  not. Live models vary in wording, and a case that depends on a subtle
  distinction (mixed intent, ambiguity, technical vs standard) is worth
  spot-checking live before demoing it.

Nothing here has been run end-to-end against a live model or a live F&O tenant.

## Fixture data (`app/mcp/d365_finance/fixtures.json`)

| Customer | Key acct | Owners | Credit |
|---|---|---|---|
| Meridian Process Systems | yes | Elena Cross (KAM), Raj Patel (sales eng), Tom Byrne (service) — no application specialist | ok |
| BASF SE | yes | Sofia Lindqvist (KAM), Marcus Feld (sales eng), Priya Nair (application), Hans Vogel (service) | ok |
| BASF Coatings GmbH | yes | none — KAM seat vacant | ok |
| Vantage Fluid Handling | no | Nina Alvarez (territory), Wei Zhang (application) | **on hold** |
| Bristow Industrial | no | none | ok |

Quotes: `QUO-2026-0042` (Meridian, PO-88213, Dura 35) · `QUO-2026-0090` (Vantage, PO-90114, Dura 65)

Orders: `SO-2025-0977` (BASF SE, customer PO **231706**, Dura 45, delivered) ·
`SO-2026-1310` (BASF SE, PO 244019, Dura 85, confirmed, not in production) ·
`SO-2026-1402` (Meridian, material shortage) · `SO-2026-1455` (BASF SE, quality hold) ·
`SO-2026-1187` (Meridian, production delay) · `SO-2026-1150` (Meridian, dispatched) ·
`SO-2026-1200` (Vantage, normal)

Stock: Dura 35 feasible (14d) · Dura 45 feasible (21d) · Dura 85 feasible (28d) ·
Dura 65 **delayed** (45d) · Special ATEX Custom **not available** (90d)

---

## A. Dual-department routing

### 1. Two separate asks in one message — the flagship case

```
Subject: Two items
Message: Dear Sir/Madam,
  1. New pump as attached data sheet: 5 pcs
  2. Spare parts for old order: PO 231706
  Kind regards, Klaus Brenner
Sender: k.brenner@basf.example
```

**Expect:** `intent=RFQ`, `secondary_intent=SPARE_PARTS`,
`secondary_reference=PO 231706` → **`route_dual_intent`**.

Names both owners from BASF SE's own record: **Sofia Lindqvist** (new equipment)
and **Hans Vogel** (aftermarket), states why both are required, and states what
each is expected to do. The single-intent RFQ ladder must *not* also run.

This is the case to lead a demo with: a message a single-intent classifier
silently answers halfway.

### 2. Safety issue

```
Subject: Pump leaking acid onto the walkway
Message: One of our pumps is leaking sulphuric acid onto the operator walkway.
  We have cordoned the area off. Please advise urgently.
Sender: hse@basf.example
```

**Expect:** `has_safety_issue=true` → **`route_safety`**, before any intent
routing. Second team **Service / QHSE (Hans Vogel)** alongside the commercial
owner, with the commercial side explicitly told to commit to no remedy or
liability until QHSE reports.

### 3. Production stoppage

```
Subject: Line 3 is down
Message: Our dosing pump failed overnight and line 3 is completely stopped.
  We need someone on site today.
Sender: maintenance@basf.example
```

**Expect:** `production_stoppage=true` → **`route_production_stoppage`**.
Service restores production while Sales owns escalation and the commercial
consequences — explicitly in parallel, not in sequence.

### 4. Product-quality complaint

```
Subject: Repeated hose failures
Message: This is the third hose failure on the same pump in two months. We want
  to know why, and we expect compensation for the downtime.
Sender: quality@basf.example
```

**Expect:** `complaint_type=product_quality` → **`complaint_product_quality`**.
Quality owns root cause and warranty position; the commercial owner decides
credit, replacement or goodwill *once the root cause is known*.

### 5. Order change that is both technical and commercial

```
Subject: Changes to order SO-2026-1310
Message: We need the wetted parts in Hastelloy rather than 316L, and we would
  also like to move to 60-day payment terms.
Sender: k.brenner@basf.example
```

**Expect:** `technical_deviation_requested=true` AND `price_or_terms_changed=true`
on an order not yet in production → **`change_commercial_and_engineering`**.
Sales Engineering (**Marcus Feld**) confirms feasibility; Order Administration
reprices. Applying one without the other produces an order that either cannot be
built or cannot be invoiced.

> A commercial-only change on the same order goes to `change_commercial`
> instead — one team, no dual block.

---

## B. Accuracy guards — the system refuses to guess

### 6. Ambiguous customer name

```
Subject: Quotation request
Message: Please quote two replacement pumps for our transfer skid. BASF
Sender: einkauf@basf.example
```

**Expect:** `find_customer` returns **2** matches (BASF SE and BASF Coatings
GmbH) → **pauses** at `customer_ambiguous`. The reviewer sees both candidates
and the match count.

The two entities have different owners, different credit positions and different
order history, so taking the first match would route silently to the wrong
person. The routing ladder must not run at all.

> Naming the exact legal entity — "BASF SE" — routes normally to Sofia Lindqvist.

### 7. A quote belonging to a different customer

```
Subject: Purchase order
Message: Please process our order against quotation QUO-2026-0042, our PO-88213.
Sender: orders@vantage-fluid.example
```

**Expect:** the quote and PO are **Meridian's**, not Vantage's →
`belongs_to_customer=false`, `po_matches_quote=false` → **`order_commercial_review`**.
Never `order_management_ready`.

Matching on quotation reference alone would have validated this cleanly.

### 8. An order naming no quotation

```
Subject: Order for 5 units
Message: Please supply five Dura 85 units to our Ludwigshafen site.
Sender: k.brenner@basf.example
```

**Expect:** **`order_missing_quote_reference`** — asks which quotation the order
is against. This is a *missing fact*, not a pricing dispute, and must not be
reported as a commercial mismatch.

### 9. An order with no identifiable product

```
Subject: Order against QUO-2026-0042
Message: Please proceed with our order, PO-88213. Same as we discussed.
Sender: orders@meridian-process.example
```

**Expect:** no `pump_model` extracted → the availability lookup never runs →
**`order_product_not_identified`**. Availability is *unknown*, not confirmed —
so this must reach neither `order_management_ready` nor `order_supply_chain_review`.

### 10. Low extraction confidence

```
Subject: pls advise
Message: need the thing for the line asap, same as before, thx
Sender: ops@bristow-industrial.example
```

**Expect:** confidence below 0.80 → **pauses** at `manual_low_confidence`, with
the reviewer shown the original message, the extracted fields, and the live
Dynamics facts (customer match and count, ownership, credit, order, fulfilment,
availability).

---

## C. Ownership and queue fallbacks

| # | Case | Customer | Expect |
|---|---|---|---|
| 11 | Standard RFQ, key account | Meridian | `rfq_named_kam` — Elena Cross |
| 12 | Technical RFQ | Meridian | `rfq_named_sales_engineer` — Raj Patel |
| 13 | Complex RFQ, no specialist on file | Meridian | `rfq_application_engineering_queue` |
| 14 | Standard RFQ, key account, KAM seat vacant | BASF Coatings GmbH | `rfq_kam_queue` — never a blank owner |
| 15 | Standard RFQ, no owners at all | Bristow Industrial | `rfq_inside_sales_queue` |
| 16 | Spare parts with a serial number | BASF SE | `spare_parts_named_owner` — Hans Vogel |
| 17 | Spare parts, no serial | Meridian | `spare_parts_need_serial` |

---

## D. Order status

| # | Reference quoted by the customer | Expect |
|---|---|---|
| 18 | `PO 231706` (their own PO, not our number) | resolves to `SO-2025-0977` — matching only our order number would wrongly report not-found |
| 19 | `SO-2026-1187` | `status_production_delay` |
| 20 | `SO-2026-1402` | `status_material_shortage` |
| 21 | `SO-2026-1455` | `status_quality_hold` |
| 22 | `SO-2026-1150` | `status_dispatched` |
| 23 | `SO-2026-1200` | `status_normal` |
| 24 | `SO-DOES-NOT-EXIST` | `status_order_not_found` |
| 25 | none quoted | `status_missing_reference` |

---

## E. Order acceptance

| # | Case | Expect |
|---|---|---|
| 26 | Meridian, `QUO-2026-0042` + `PO-88213`, Dura 35 | `order_management_ready` |
| 27 | Same, but Dura 65 (delayed stock) | `order_supply_chain_review` |
| 28 | Same, plus a technical deviation | `order_technical_review` |
| 29 | Meridian, wrong PO | `order_commercial_review` |
| 30 | Vantage, `QUO-2026-0090` + `PO-90114`, Dura 65 | `order_credit_hold` — stopped before order management |
| 31 | Unknown company | `order_customer_not_found` |

---

## Every routed outcome ends with

```
Contact: <person who wrote in>
Company (verified in Dynamics 365): <name we resolved>
Company (as stated by the customer): <name they used>
```

The verified and stated names are kept separate deliberately: when they differ,
whoever picks the case up can see it rather than having it silently reconciled.
An unmatched company leaves the verified line empty rather than echoing back the
customer's own wording as though it had been confirmed.

---

## F. The six-case demo

Six cases that progressively remove what the previous one relied on. Pinned by
`test_demo_1_*` … `test_demo_6_*`.

| # | Case | Shows |
|---|---|---|
| 1 | Standard Dutch RFQ, Meridian | Named salesperson — Elena Cross |
| 2 | German technical RFQ, Meridian | Sales Engineer — Raj Patel |
| 3 | PO does not match its quotation | Commercial review; order creation **blocked** |
| 4 | Order held by `QUALITY_HOLD` | Quality, *not* generic Supply Chain — the ERP reason code decides the function |
| 5 | Leaking pump + certificate + replacement quote | One parent case, three child work items across three teams |
| 6 | Production stopped, solvent leaking, **ERP unreachable** | Emergency routes anyway |

Case 6 is the one to end on. Confidence is 0.55, the customer name matches two
accounts, and Dynamics is down — all three used to be able to hold the case:

```
WHY ROUTED?

Safety risk                TRUE   (from the message)
Production stopped         TRUE   (from the message)
Dynamics 365 lookup        error
Customer identity          unresolved (0 matches returned)
Extraction confidence      0.55

Decision:
Emergency routing does not depend on Dynamics 365 availability.

Primary team:      Service Emergency queue
Supporting:        Quality + EHS / Safety
Human review:      Required
Automatic commercial action: BLOCKED
```

---

## G. Unknown is not "no"

`MCPToolAgent` reports a failed call as `found: false` with empty data, so every
ERP-derived boolean could be manufactured out of an outage. Each of these now
routes to a person instead:

| Lookup fails | Old outcome | Now |
|---|---|---|
| `find_customer` | "new customer" → onboarding | `order_customer_system_unavailable` |
| `get_credit_status` | "not on credit hold" → order accepted | `order_credit_unknown` |
| `get_sales_order` (status) | "order does not exist" told to the customer | `status_system_unavailable` |
| `get_order_fulfilment_status` | **"running normally"** by router fallback | `status_fulfilment_unknown` |
| `get_sales_order` (change) | "order does not exist" | `change_system_unavailable` |
| production state | "production has not started" → change promised | `change_production_status_unknown` |

A lookup that genuinely answered "no such customer" still routes to customer
review — the guard distinguishes silence from a negative, it does not turn every
negative into silence.

---

## H. What the workflow deliberately does not decide

| Situation | Why it stops at a human |
|---|---|
| Warranty claim on a lapsed unit | Expiry on file is evidence, not a contractual ruling — goodwill, service agreements and pre-expiry faults all still apply |
| Serial number belonging to another customer | Needs a person, and the record must not be quoted back |
| Credit position unknown | Only Finance releases against an unverified position |
| PO that does not match its quotation | Commercial commitment |
| Any emergency | Commercial action blocked pending review |

Customer-asserted status changes none of these. The extraction schema is the
guarantee: it contains no `key_account`, `credit_hold`, `warranty_active` or
`route` field, so no wording can supply one. A test asserts that stays true.

## Known gaps

- In the two-asks case the secondary reference (`PO 231706`) is stated in the
  outcome but not looked up in Dynamics: the extractor puts it in
  `secondary_reference` while `get_sales_order` reads `sales_order_reference`.
  Resolving it needs a lookup-ordering change. Single-intent messages quoting a
  customer PO **do** resolve.
- `find_customer` matches on `contains`, so ambiguity is detected by match count
  rather than by any notion of legal-entity identity. Two unrelated companies
  sharing a substring would pause as ambiguous.
- No product-knowledge/RAG step exists in this workflow; product questions route
  to a person rather than being answered from a product corpus.
- No product-knowledge/RAG step; product questions route to a person rather
  than being answered from a product corpus.

### Next capability — cross-document reference conflict

The workflow reads message fields only. If the body says `SO-1045` and the
attached PO says `SO-1054`, nothing notices. The fix is *not* to ask a model
which number looks right — it is to collect references per source and resolve
them against the ERP as a first-class state:

```
references:
  message_order_ids:     [SO-1045]
  attachment_order_ids:  [SO-1054]
  purchase_order_ids:    [PO-30091]

reference_resolution:
  status: CONFLICT           # MATCH | NOT_FOUND | CONFLICT | SYSTEM_ERROR
  authoritative_matches:     [SO-1054]
```

`CONFLICT` routes to Order Management review, the same way `UNKNOWN` already
routes rather than being collapsed into a negative. The platform supports file
extraction; it is simply not wired into this YAML.

### Platform roadmap — not workflow config

Two gaps are architectural and deliberately not forced into this workflow:

**Case lifecycle (duplicate / correction / supersede).** Three messages —
"order 100 pumps", "correction, make that 10", "please ignore my previous
message" — cannot be resolved by three independent runs, because none of them
can see the others. This needs shared business-level state above the workflow:

```
Mailbox / event  →  Case Manager  →  workflow execution
                    (correlate, version, supersede)
```

**Child run orchestration.** `work_items` are addressable objects inside one
execution, not child runs. A `SpawnWorkflowNode` would have to answer: what
happens when child 1 succeeds and child 2 fails; can the parent cancel
children; who owns retries; are child costs rolled up; does the parent await
all; how is a child's HITL pause represented; can one child be closed manually
while another runs; how are permissions inherited. "Not implemented" is a
better answer than a `spawn_run()` call that pretends those are settled.
