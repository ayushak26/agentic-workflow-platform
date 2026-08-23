# 12 sample inputs for testing routing logic

Workflow: `workflows/crm_aware_customer_triage.yaml` (CRM-Aware Customer Request Triage).

> Not the pump manufacturer workflow. `workflows/pump_manufacturer_case_routing.yaml`
> has its own cases in `eval/pump_manufacturer_routing_cases.md`, against a different
> fixture set (`app/mcp/d365_finance/fixtures.json`) and a different schema — the two
> share no fields and no customers.

Each case below has been **run live against the real workflow** (real LLM extraction,
fixture-backed Dynamics 365 CRM — not stubbed) to confirm the outcome shown actually
happens. Paste `subject` / `message` / `sender_email` into the Run dialog for this
workflow in the UI, then open the run in Cockpit to watch it work through
understanding → CRM lookup → decision → routing.

Live models have some run-to-run variance in wording, so field values like
`request_summary` will differ slightly each time — the **routing outcome** (intent,
complexity, human_review, route) is what these cases are designed to pin down.

Known CRM accounts in the fixture data (`app/mcp/dynamics/fixtures.json`), for
reference: **ABC Chemicals B.V.** (1 past order, 1 open opportunity), **ABC Chemicals
GmbH** (2 past orders, 1 open + 1 closed opportunity), **Nordvand Process AS** (no
past orders, no opportunities), **Verder Liquids Demo Account** (no history).

---

### 1. Standard — routine quotation, known customer, no ambiguity

```
Subject: Quotation request – 2x Dura 15 pumps
Message: Hello, this is ABC Chemicals B.V. We would like a quotation for two Dura 15
peristaltic pumps for a new transfer line. Please include lead time. Thank you.
Sender: purchasing@abc-chemicals-bv.example
```
**Expect:** `primary_intent=quotation_request`, `complexity=standard`,
`human_review=False` → routes to **Sales**. `customer_known=True`,
`has_open_opportunity=True`.

---

### 2. Technical — production-impact diagnosis + a quote, single unambiguous account

```
Subject: Dringend: technische Unterstützung benötigt
Message: Guten Tag, hier ist ABC Chemicals GmbH. Wir benötigen dringend technische
Unterstützung: Unsere Verderflex Dura 35 zeigt starke Druckschwankungen und wir
möchten die Ursache diagnostizieren. Vorsichtshalber haben wir die Linie gestoppt.
Bitte senden Sie uns außerdem ein Angebot für zwei Ersatzpumpen.
Sender: einkauf@abc-chemicals-gmbh.example
```
**Expect:** `primary_intent=technical_support`, `complexity=technical`,
`human_review=False` → routes to **Technical Support** (triggers the product-knowledge
RAG lookup). `customer_known=True`.

*Note: an earlier wording of this case ("our line is down because of the pump")
got classified as a **complaint** instead — a defect report and a diagnostic request
read very similarly to a real model. Worth trying that phrasing too, live, to see the
system's complaint-priority rule fire instead.*

---

### 3. Complex — ATEX / hazardous area, known customer

```
Subject: Pompe pour zone ATEX
Message: Bonjour, nous sommes ABC Chemicals B.V. Nous avons besoin d'une pompe pour
une zone classée ATEX, avec plusieurs fluides corrosifs et une pression variable.
Merci de nous conseiller.
Sender: engineering@abc-chemicals-bv.example
```
**Expect:** `complexity=complex`, `human_review=True`, escalation reason mentions
needing a Product/Application Specialist → routes to **a person**. The RAG/technical
branch is skipped entirely — complex cases never reach it.

---

### 4. Ambiguous — vague reference to a past order, at an account with no order history

```
Subject: Re: spare part
Message: Hi, this is Nordvand Process AS. Could you send us another one of the usual,
same as last time? No need for a full spec, you have it on file.
Sender: maintenance@nordvand.example
```
**Expect:** `human_review=True`, `product_resolved=False` → routes to **a person**.
Nordvand has zero orders on file, so the system correctly refuses to guess.

---

### 5. Order status inquiry

```
Subject: Order status
Message: Hi, could you give us an update on our recent order? Our line is waiting on
it and we'd like to know when it will arrive.
Sender: ops@nordvand.example
```
**Expect:** `primary_intent=order_status`, `human_review=False` → routes to **Sales**.
`customer_known=True` — note the company was never named in the message; it's
inferred from the sender's email domain.

---

### 6. Complaint — always reaches a person, regardless of anything else asked

```
Subject: Faulty pump — Dura 15
Message: This is ABC Chemicals B.V. The Dura 15 pump you delivered is leaking
constantly from the housing. This is unacceptable, please advise how you will
resolve it.
Sender: quality@abc-chemicals-bv.example
```
**Expect:** `primary_intent=complaint`, `human_review=True`, reason: "Complaints are
reviewed by a person before any reply." → routes to **a person**.

---

### 7. Multi-account ambiguity — company name matches more than one CRM account

```
Subject: Quotation request
Message: Hello, we are ABC Chemicals. Please send us a quotation for two replacement
pumps.
Sender: purchasing@abc-chemicals.example
```
**Expect:** `human_review=True`, reason: "More than one CRM account matches this
company name..." → routes to **a person**. ("ABC Chemicals" with no legal suffix
matches both the B.V. and the GmbH account.)

---

### 8. Multi-order ambiguity — vague reference at an account with two past orders

```
Subject: Spare part needed
Message: This is ABC Chemicals GmbH. Please send us the same spare part as our last
order — you should have the details on file.
Sender: einkauf@abc-chemicals-gmbh.example
```
**Expect:** `human_review=True`, `product_resolved=False`, reason: "...more than one
past order could be meant. The order history is shown below..." → routes to **a
person**, with the two candidate orders shown for them to pick from.

---

### 9. Spare part, no model, no history, not referencing a past order

```
Subject: Spare parts order
Message: This is Nordvand Process AS. We would like to order a replacement gasket kit
for one of our peristaltic pumps, but we don't have the pump's model number or serial
number on hand right now. Please advise what you need from us to process a spare
parts order and send a quote.
Sender: maintenance@nordvand.example
```
**Expect:** `primary_intent=spare_part_request`, `human_review=True`,
`product_resolved=False`, reason: "A spare part cannot be identified without a
product model." → routes to **a person**. Distinct rule from cases 4/8 — here there's
no order history to fall back on *at all*.

---

### 10. Known customer with an open opportunity — flagged, but not escalated

```
Subject: New pump quotation
Message: This is ABC Chemicals GmbH. We'd like a quotation for a new Dura 35 pump for
a second production line.
Sender: einkauf@abc-chemicals-gmbh.example
```
**Expect:** `human_review=False`, `has_open_opportunity=True` → routes to **Sales**.
Confirms an open opportunity is surfaced as context for Sales, but doesn't by itself
trigger review — only real red flags do.

---

### 11. Unclear, terse message from an unrecognizable address

```
Subject: quote
Message: need quote 2 units asap thx
Sender: buyer@gmail.com
```
**Expect:** `human_review=True`, `customer_known=False` → routes to **a person**.
A generic consumer email domain gives no company signal, so `organization` correctly
stays null rather than being guessed — a good live demo of that specific safeguard.

---

### 12. Availability-only question, no price ask

```
Subject: Disponibilidad de bombas
Message: Somos ABC Chemicals B.V. ¿Podrían indicarnos el plazo de entrega para dos
bombas Dura 15? Por ahora no necesitamos precio, solo disponibilidad.
Sender: compras@abc-chemicals-bv.example
```
**Expect:** `primary_intent=general_inquiry`, `human_review=False` at the rule level,
but **still routes to a person** — a genuine, worth-discussing gap: a pure
availability/lead-time question (no price ask) doesn't match the schema's
`quotation_request` bucket, so it falls into `general_inquiry`, which has no
automated route and hits the router's catch-all fallback. Whether that should instead
auto-route to Sales (since it's commercially routine) is a real design decision, not a
bug — worth raising in the interview as a design trade-off you noticed and can defend
either way.
