# Pump Customer Routing — three levels of the same idea

**Files**

| Level | Workflow | Nodes | Tests |
|---|---|---|---|
| 1 · Simple | [`workflows/pump_routing_level_1.yaml`](../../workflows/pump_routing_level_1.yaml) | 8 | `tests/test_pump_routing_levels.py` |
| 2 · Context-Aware | [`workflows/pump_routing_level_2.yaml`](../../workflows/pump_routing_level_2.yaml) | 19 | `tests/test_pump_routing_levels.py` |
| 3 · Production | [`workflows/pump_manufacturer_case_routing.yaml`](../../workflows/pump_manufacturer_case_routing.yaml) | 32 | `tests/test_pump_manufacturer_case_routing.py` |

All three answer one question: **a multilingual customer message has arrived, who
should handle it?** All three route to the same five business functions. What
grows between them is the evidence behind the decision — not the vocabulary, and
not the architecture.

> Level 3 **is** the production workflow: it took over
> `workflows/pump_manufacturer_case_routing.yaml` from the previous intent-first,
> emergency-led graph (114 nodes, 35 routers). That graph is archived at
> [`workflows/test_fixtures/pump_case_routing_v1.yaml`](../../workflows/test_fixtures/pump_case_routing_v1.yaml)
> with its 105 tests (`tests/test_pump_case_routing_v1.py`), which are kept for
> the platform behaviour they cover — `rule`-mode routers, timeout-bounded MCP
> calls and mid-graph human gates — not because the routing design is still
> current. It no longer appears in the workflow Library.

---

## 1. The departments

```text
SALES                         new equipment, quotations, commercial terms
SUPPLY_CHAIN                  execution of orders that already exist
PRODUCT_SERVICE               the installed base: support, service, spare parts
CUSTOMER_SUPPORT              information, clarification, coordination, triage
OTHER                         Finance · Quality · Order Management · Engineering
```

`OTHER` is for genuine specialist authority. It is never the bin for a case the
rules could not place — those go to Customer Support triage, where a person
routes them.

---

## 2. The one architectural rule

At every level:

```text
AI     understands the message         → intent, lifecycle stage, references
Rules  decide who owns it              → department, sub-team, case type
D365   supplies the facts (L2, L3)     → customer, order, fulfilment, unit, owner
Human  handles what neither can (L3)   → ambiguity, mismatch, low confidence
```

The extraction schema at every level contains no `department`, `team`, `owner` or
`route` field — asserted by `test_no_level_lets_the_model_choose_the_department`.
Exactly one model call is made per run, at every level.

---

## 3. What each level adds

| Capability | Level 1 | Level 2 | Level 3 |
|---|:--:|:--:|:--:|
| Multilingual understanding | ✓ | ✓ | ✓ |
| Structured extraction | 8 fields | 12 fields | 26 fields |
| Deterministic department routing | ✓ | ✓ | ✓ |
| Lifecycle stage as a routing signal | ✓ | ✓ | ✓ |
| Dynamics customer lookup | | ✓ | ✓ |
| Order + fulfilment context | | ✓ | ✓ |
| Installed-unit lookup | | ✓ | ✓ |
| Named account owner | | ✓ | ✓ |
| Quotation reconciliation | | | ✓ |
| Stock availability as supporting evidence | | | ✓ |
| ERP failure handling | | basic | full (`FOUND`/`NOT_FOUND`/`ERROR`/`AMBIGUOUS`/`SKIPPED`) |
| Customer ambiguity | | flagged | blocks before any account data is read |
| Inactive owner fallback | | ✓ | ✓ with recorded name + status + team |
| Cross-customer serial protection | | | ✓ |
| Warranty nuance | | | ✓ |
| Multi-intent work items | | | ✓ |
| Explainable routing evidence | reason | reason + facts | reason + why-not + full evidence |
| Human review gates | 0 | 0 | 4 |

---

## 4. Level 1 — the idea in eight nodes

```text
understand_message → department_decision → department_router → 5 outcomes
```

No lookups, no connections, nothing to configure. It already demonstrates the two
corrections a business audience recognises immediately:

| Message | Naive answer | Level 1 answer | Why |
|---|---|---|---|
| "How much is a replacement seal?" | Sales (it says *price*) | **Product Service / Spare Parts** | a part for a pump they already own |
| "What delivery time for 10 new pumps?" | Supply Chain (it says *delivery*) | **Sales** | no order exists to execute |

---

## 5. Level 2 — the same sentence, decided by Dynamics

```text
understand_message → find_customer → customer_confirmed
                        → ownership · order · fulfilment · installed unit
                        → business_context → routing_decision
                        → department_router → named owner | team queue
```

The demonstration is one sentence with two answers:

> *"When will the pump arrive?"*

```text
Dynamics has no order              → SALES / Inside Sales      (lead-time question)
SO-2026-1402, MATERIAL_SHORTAGE    → SUPPLY_CHAIN / Material Planning
SO-2026-1455, QUALITY_HOLD         → OTHER / Quality
SO-2025-0977, DISPATCHED           → CUSTOMER_SUPPORT / Logistics Support
```

Same words, same intent, four owners — chosen by the ERP's own reason.

---

## 6. Level 3 — production shape

```text
understand_message
      ↓
find_customer  →  customer_state_router ──ambiguous──→ customer_clarification (human)
      ↓ (unambiguous)
customer_confirmed
      ↓
ownership · order · fulfilment · installed unit · quotation · stock   (6 parallel reads)
      ↓
business_facts        FOUND / NOT_FOUND / SYSTEM_ERROR / NO_REFERENCE / MISMATCH …
      ↓
routing_decision      41 ordered rules: intent proposes → facts correct → overrides win
      ↓
assignment_decision   a name only when the directory says it is assignable
      ↓
multi_intent_router ──several asks──→ work_item_plan → multi_intent_case
      ↓ (one ask)
primary_department_router
      ├── SALES            → named owner | team queue
      ├── SUPPLY_CHAIN     → planning case
      ├── PRODUCT_SERVICE  → named owner | queue | serial verification (human)
      ├── CUSTOMER_SUPPORT → support case | first-line triage (human)
      └── OTHER            → specialist case | specialist review (human)
```

### Why the rules live in one node

`routing_decision` is a single `DecisionAgent` holding every department rule, in
three ordered layers:

```text
LAYER A   the intent proposes a department      RFQ → Sales, SPARE_PARTS → Service …
LAYER B   business facts correct it             no order → Sales; QUALITY_HOLD → Quality
LAYER C   overrides win outright                no ask, system down, low confidence
```

Later rules override earlier ones, so the file reads top to bottom as the
business reasons about the case. The alternative — a router per distinction — is
what produced the 113-node predecessor: 35 routers and 61 terminal nodes for the
same number of real business decisions.

A rule that *moves* a case to another department also clears
`assignment_track`, so a Customer Support triage case never arrives with the
service owner's name on it.

### Unknown is not "no"

Every lookup is normalised into a named state before any rule reads it:

```text
CRM unreachable        → customer_state SYSTEM_ERROR      (never "new customer")
order lookup failed    → order_state SYSTEM_ERROR         (never "order not found")
ownership failed       → owner_status unknown_lookup_failed (never "unassigned")
serial not on record   → serial_state NOT_FOUND           (never "not our pump")
serial another account → serial_state MISMATCH            (nothing is disclosed)
```

### Explainability

Every outcome packet carries the same five blocks — `routing`, `assignment`,
`classification`, `evidence`, `decision` — plus `next_action` and
`requires_human`. `decision.why_not` answers the question a reviewer actually
asks:

```yaml
routing:    {primary_department: PRODUCT_SERVICE, sub_team: Spare Parts, …}
decision:
  reason:   ["The customer needs a component for equipment they already own."]
  why_not:  "Not Sales: the price of a replacement part for an installed pump is
             after-sales work, not a new-equipment quotation."
evidence:   {serial_state: VERIFIED, warranty_state: ACTIVE, order_state: NO_REFERENCE, …}
```

---

## 7. Demo script

| | Level 1 | Level 2 | Level 3 |
|---|---|---|---|
| **Sales** | "Please quote 5 pumps." → Sales | "…for 30% NaOH at 80 °C." → Sales Engineering (Raj Patel) | Customer names a salesperson who has left → Sales queue, with her name and status shown |
| **Supply Chain** | "Where is SO-12021?" → Supply Chain | ERP says MATERIAL_SHORTAGE → Material Planning | ERP does not answer → Customer Support, status **unknown**, nothing invented |
| **Product Service** | "Our pump isn't working." → Product Service | Serial verified → named service owner | Serial belongs to another customer → stops for a person, discloses nothing |
| **Customer Support** | "Send the datasheet." → Customer Support | "Send the certificate." → Quality | Order reference unverifiable because the ERP is down → no fabricated status |
| **Multi-department** | — | — | One email, three asks → three work items, three departments, one coordinated reply |

---

## 8. Known limitations

1. **A silent PO/quotation mismatch is not detectable.** `get_quote` exposes the
   quote's status, owner, linked PO and pump model — no price and no quantity. A
   PO for 8 units against a quotation for 5 is caught only when the customer says
   so in the message (`price_or_terms_changed` / `technical_change_requested`).
   Catching it from data needs quantity and price on the quote tool.
2. **Work items are a department map, not a list of child runs.** The engine has
   no per-item iteration, so `multi_intent_case` names every department involved
   and coordinates one reply. It does not spawn a run per ask.
3. **No invoice lookup exists.** Invoice queries route to Finance on intent
   alone; nothing is verified against a financial record.
4. **`get_credit_status` is not called.** Credit affects order acceptance, not
   who owns the case, so Level 3 does not spend the call.
5. **Level 3 does not reply to the customer.** It routes, and stops.
6. **Extraction quality is assumed, not tested.** The suites script the model's
   output so the routing assertions are exact. How reliably a real model fills
   `lifecycle_stage` — the single most load-bearing field — belongs in a separate
   extraction evaluation.
7. **The v1 graph is archived, not deleted.** It is out of the Library but its
   suite still runs, so a platform change that breaks 35 routers or the
   emergency lookups is still caught.
