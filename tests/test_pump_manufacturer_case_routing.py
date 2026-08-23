"""Routing tests for workflows/pump_manufacturer_case_routing.yaml (Level 3).

The workflow's job is one question — *who in the company should handle this
message?* — so every test here asserts a business outcome: the owning
department, the sub-team, the named owner or the queue that replaces one, the
reason, and whether a person is required.

Real rules, real routers, real MCP policy dispatch and the real
`app/mcp/d365_finance` handlers over the bundled fixtures. Only the model call
and the MCP transport are faked (see tests/helpers/pump_routing.py).

The suite deliberately spends most of its weight on cases that route WRONG
under naive classification:

  * the same word in two lifecycle stages ("price", "delivery", "problem")
  * the same customer sentence with two different ERP states behind it
  * a business system that did not answer, versus one that answered "no"
  * a customer asserting facts the CRM contradicts
  * one message containing three separate asks
"""
from __future__ import annotations

import pytest

from tests.helpers.pump_routing import (
    AMBIGUOUS,
    BASF,
    BRISTOW,
    MERIDIAN,
    VANTAGE,
    decisions,
    exit_packet,
    hitl_context,
    outputs,
    reached,
    routing,
    run_level_3,
)


def _assignment(result, node_id):
    return exit_packet(result, node_id)["assignment"]


def _reason_text(result) -> str:
    return " ".join(routing(result)["reason"])


# ===========================================================================
# SALES
# ===========================================================================


async def test_a_standard_rfq_reaches_the_account_owner():
    result = await run_level_3(
        {"primary_intent": "RFQ", "customer_name": MERIDIAN, "lifecycle_stage": "presales"},
        "sales-standard",
    )

    assert result["status"] == "completed"
    assert routing(result)["primary_department"] == "SALES"
    assert routing(result)["sub_team"] == "Inside Sales"
    assert _assignment(result, "sales_named_owner")["owner_name"] == "Elena Cross"
    assert _assignment(result, "sales_named_owner")["owner_status"] == "active"
    assert routing(result)["requires_human"] is False


async def test_a_technical_rfq_reaches_the_sales_engineer():
    result = await run_level_3(
        {"primary_intent": "RFQ", "customer_name": MERIDIAN, "lifecycle_stage": "presales",
         "technical_complexity": "technical"},
        "sales-technical",
    )

    assert routing(result)["sub_team"] == "Sales Engineering"
    assert routing(result)["case_type"] == "TECHNICAL_RFQ"
    assert _assignment(result, "sales_named_owner")["owner_name"] == "Raj Patel"


async def test_a_complex_application_reaches_the_application_specialist():
    result = await run_level_3(
        {"primary_intent": "PRODUCT_SELECTION", "customer_name": BASF, "lifecycle_stage": "presales",
         "technical_complexity": "complex"},
        "sales-complex",
    )

    assert routing(result)["sub_team"] == "Application Engineering"
    assert routing(result)["case_type"] == "COMPLEX_APPLICATION"
    assert _assignment(result, "sales_named_owner")["owner_name"] == "Priya Nair"


async def test_a_territory_account_with_no_key_account_owner_reaches_its_territory_owner():
    result = await run_level_3(
        {"primary_intent": "RFQ", "customer_name": VANTAGE, "lifecycle_stage": "presales"},
        "sales-territory",
    )

    assert routing(result)["primary_department"] == "SALES"
    assert _assignment(result, "sales_named_owner")["owner_name"] == "Nina Alvarez"


async def test_changed_commercial_terms_go_to_sales_not_to_order_management():
    """"Change this quote from EXW to DDP and give us 5% more discount" is a
    commercial negotiation, whatever administrative words surround it."""
    result = await run_level_3(
        {"primary_intent": "ORDER_CHANGE", "customer_name": BASF, "price_or_terms_changed": True,
         "sales_order_reference": "SO-2026-1310", "lifecycle_stage": "order_execution"},
        "sales-terms",
    )

    assert routing(result)["primary_department"] == "SALES"
    assert routing(result)["sub_team"] == "Commercial Sales"
    assert routing(result)["requires_human"] is True
    assert "commercial agreement" in _reason_text(result)


async def test_a_ga_drawing_request_goes_to_sales_engineering_not_customer_support():
    """The document type decides the owner: a GA drawing carries the technical
    configuration of a specific offer."""
    result = await run_level_3(
        {"primary_intent": "DOCUMENT_REQUEST", "customer_name": MERIDIAN,
         "requested_document_type": "GA_DRAWING"},
        "sales-drawing",
    )

    assert routing(result)["primary_department"] == "SALES"
    assert routing(result)["sub_team"] == "Sales Engineering"
    assert _assignment(result, "sales_named_owner")["owner_name"] == "Raj Patel"


async def test_a_purchase_order_naming_no_quotation_asks_which_quotation():
    """A missing fact, not a pricing dispute — the distinction matters because
    the second one is an escalation and the first one is a question."""
    result = await run_level_3(
        {"primary_intent": "NEW_ORDER", "customer_name": MERIDIAN,
         "customer_po_reference": "PO-88213", "lifecycle_stage": "presales"},
        "sales-po-no-quote",
    )

    assert routing(result)["primary_department"] == "SALES"
    assert routing(result)["case_type"] == "ORDER_WITHOUT_QUOTATION"
    assert "which quotation" in routing(result)["next_action"]
    assert "not a commercial dispute" in routing(result)["why_not"].lower()


async def test_a_purchase_order_that_does_not_match_its_quotation_goes_to_commercial_review():
    result = await run_level_3(
        {"primary_intent": "NEW_ORDER", "customer_name": MERIDIAN,
         "quotation_reference": "QUO-2026-0042", "customer_po_reference": "PO-WRONG-NUMBER",
         "lifecycle_stage": "presales"},
        "sales-po-mismatch",
    )

    assert decisions(result, "business_facts")["quote_state"] == "PO_MISMATCH"
    assert routing(result)["case_type"] == "PO_QUOTE_MISMATCH"
    assert routing(result)["sub_team"] == "Commercial Sales"
    assert routing(result)["requires_human"] is True


async def test_a_quotation_belonging_to_another_customer_never_validates_an_order():
    result = await run_level_3(
        {"primary_intent": "NEW_ORDER", "customer_name": VANTAGE,
         "quotation_reference": "QUO-2026-0042", "customer_po_reference": "PO-88213",
         "lifecycle_stage": "presales"},
        "sales-cross-customer-quote",
    )

    assert decisions(result, "business_facts")["quote_state"] == "NOT_THIS_CUSTOMER"
    assert routing(result)["requires_human"] is True


async def test_a_matching_purchase_order_is_ordinary_sales_work():
    result = await run_level_3(
        {"primary_intent": "NEW_ORDER", "customer_name": MERIDIAN,
         "quotation_reference": "QUO-2026-0042", "customer_po_reference": "PO-88213",
         "lifecycle_stage": "presales"},
        "sales-po-match",
    )

    assert decisions(result, "business_facts")["quote_state"] == "MATCHED"
    assert routing(result)["case_type"] == "NEW_EQUIPMENT_ENQUIRY"
    assert routing(result)["requires_human"] is False


# ===========================================================================
# SUPPLY CHAIN
# ===========================================================================


async def test_a_material_shortage_reaches_material_planning():
    result = await run_level_3(
        {"primary_intent": "ORDER_STATUS", "customer_name": MERIDIAN,
         "sales_order_reference": "SO-2026-1402", "lifecycle_stage": "order_execution",
         "order_reference_present": True},
        "supply-shortage",
    )

    assert routing(result)["primary_department"] == "SUPPLY_CHAIN"
    assert routing(result)["sub_team"] == "Material Planning"
    assert routing(result)["priority"] == "HIGH"
    packet = exit_packet(result, "supply_chain_case")
    assert packet["fulfilment_status"] == "MATERIAL_SHORTAGE"
    assert packet["order_reference"] == "SO-2026-1402"


async def test_a_production_delay_reaches_production_planning():
    result = await run_level_3(
        {"primary_intent": "DELIVERY_QUERY", "customer_name": MERIDIAN,
         "sales_order_reference": "SO-2026-1187", "lifecycle_stage": "order_execution",
         "order_reference_present": True},
        "supply-delay",
    )

    assert routing(result)["primary_department"] == "SUPPLY_CHAIN"
    assert routing(result)["sub_team"] == "Production Planning"


async def test_an_expedite_request_reaches_planning_and_promises_nothing():
    result = await run_level_3(
        {"primary_intent": "ORDER_CHANGE", "customer_name": MERIDIAN,
         "sales_order_reference": "SO-2026-1310", "requested_delivery_date": "2026-10-15",
         "lifecycle_stage": "order_execution"},
        "supply-expedite",
    )

    assert routing(result)["primary_department"] == "SUPPLY_CHAIN"
    assert routing(result)["case_type"] == "EXPEDITE_REQUEST"
    assert "before anything is promised" in routing(result)["next_action"]


async def test_the_same_customer_sentence_splits_on_the_erp_reason():
    """§14/§31, stated as one test: identical wording, identical intent, two
    different owners — because Dynamics reports two different reasons."""
    shortage = await run_level_3(
        {"primary_intent": "ORDER_STATUS", "customer_name": MERIDIAN,
         "sales_order_reference": "SO-2026-1402", "lifecycle_stage": "order_execution"},
        "split-shortage",
    )
    quality_hold = await run_level_3(
        {"primary_intent": "ORDER_STATUS", "customer_name": BASF,
         "sales_order_reference": "SO-2026-1455", "lifecycle_stage": "order_execution"},
        "split-quality",
    )

    assert routing(shortage)["primary_department"] == "SUPPLY_CHAIN"
    assert routing(shortage)["sub_team"] == "Material Planning"
    assert routing(quality_hold)["primary_department"] == "OTHER"
    assert routing(quality_hold)["sub_team"] == "Quality"
    assert "cannot release" in routing(quality_hold)["why_not"]


# ===========================================================================
# PRODUCT SERVICE / SPARE PARTS
# ===========================================================================


async def test_a_verified_serial_reaches_the_named_service_owner():
    result = await run_level_3(
        {"primary_intent": "TECHNICAL_SUPPORT", "customer_name": BASF,
         "serial_number": "SN-99123", "lifecycle_stage": "installed_base"},
        "service-named",
    )

    assert routing(result)["primary_department"] == "PRODUCT_SERVICE"
    assert _assignment(result, "service_named_owner")["owner_name"] == "Hans Vogel"
    unit = exit_packet(result, "service_named_owner")["installed_unit"]
    assert unit["verified"] is True
    assert unit["pump_model"] == "Verderflex Dura 85"
    assert unit["install_site"] == "Antwerp"


async def test_a_replacement_component_is_spare_parts_work():
    result = await run_level_3(
        {"primary_intent": "SPARE_PARTS", "customer_name": BASF, "serial_number": "SN-44120",
         "lifecycle_stage": "installed_base"},
        "service-parts",
    )

    assert routing(result)["primary_department"] == "PRODUCT_SERVICE"
    assert routing(result)["sub_team"] == "Spare Parts"
    assert routing(result)["case_type"] == "SPARE_PART_REQUEST"


async def test_an_expired_warranty_is_evidence_not_a_rejection():
    result = await run_level_3(
        {"primary_intent": "TECHNICAL_SUPPORT", "customer_name": BASF, "serial_number": "SN-70001",
         "lifecycle_stage": "installed_base"},
        "service-warranty",
    )

    assert decisions(result, "business_facts")["warranty_state"] == "EXPIRED"
    assert routing(result)["primary_department"] == "PRODUCT_SERVICE"
    assert routing(result)["sub_team"] == "Service · Warranty"
    # The claim is assessed, not refused.
    assert "on its merits" in routing(result)["next_action"]
    assert exit_packet(result, "service_named_owner")["evidence"]["warranty_state"] == "EXPIRED"


async def test_a_serial_belonging_to_another_account_stops_for_a_person_and_leaks_nothing():
    """SN-44120 is BASF's. Meridian quoting it is a routine transcription
    error — and the reply must not describe BASF's site or configuration."""
    result = await run_level_3(
        {"primary_intent": "TECHNICAL_SUPPORT", "customer_name": MERIDIAN,
         "serial_number": "SN-44120", "lifecycle_stage": "installed_base"},
        "service-mismatch",
    )

    assert result["status"] == "paused"
    assert decisions(result, "business_facts")["serial_state"] == "MISMATCH"
    assert routing(result)["requires_human"] is True

    unit = outputs(result)["get_installed_unit"]["first"]
    assert unit == {"serial_number": "SN-44120", "belongs_to_customer": False}
    rendered = str(hitl_context(result))
    assert "BASF" not in rendered
    assert "Ludwigshafen" not in rendered


async def test_a_serial_we_cannot_place_asks_the_customer_rather_than_denying_the_pump():
    result = await run_level_3(
        {"primary_intent": "SPARE_PARTS", "customer_name": BASF, "serial_number": "SN-NOT-REAL",
         "lifecycle_stage": "installed_base"},
        "service-serial-unknown",
    )

    assert decisions(result, "business_facts")["serial_state"] == "NOT_FOUND"
    assert routing(result)["primary_department"] == "CUSTOMER_SUPPORT"
    assert routing(result)["sub_team"] == "Service Triage"
    assert "not tell them the pump is not ours" in routing(result)["next_action"]


async def test_after_sales_work_with_nothing_to_identify_the_unit_asks_for_the_serial():
    result = await run_level_3(
        {"primary_intent": "SPARE_PARTS", "customer_name": BASF, "lifecycle_stage": "installed_base"},
        "service-no-serial",
    )

    assert routing(result)["case_type"] == "UNIDENTIFIED_UNIT"
    assert routing(result)["primary_department"] == "CUSTOMER_SUPPORT"
    assert "serial number or model" in routing(result)["next_action"]


# ===========================================================================
# CUSTOMER SUPPORT
# ===========================================================================


async def test_a_dispatched_order_is_a_logistics_question_not_a_planning_one():
    result = await run_level_3(
        {"primary_intent": "DELIVERY_QUERY", "customer_name": BASF,
         "sales_order_reference": "SO-2025-0977", "lifecycle_stage": "order_execution"},
        "support-dispatched",
    )

    assert routing(result)["primary_department"] == "CUSTOMER_SUPPORT"
    assert routing(result)["sub_team"] == "Logistics Support"
    assert "nothing remains to plan" in routing(result)["why_not"]


async def test_an_order_progressing_normally_is_answered_by_customer_support():
    result = await run_level_3(
        {"primary_intent": "ORDER_STATUS", "customer_name": BASF,
         "sales_order_reference": "SO-2026-1310", "lifecycle_stage": "order_execution"},
        "support-normal",
    )

    assert routing(result)["primary_department"] == "CUSTOMER_SUPPORT"
    assert routing(result)["sub_team"] == "Order Support"
    assert exit_packet(result, "support_case")["evidence"]["fulfilment_state"] == "NORMAL"


async def test_an_unmatched_order_reference_never_tells_the_customer_the_order_does_not_exist():
    result = await run_level_3(
        {"primary_intent": "ORDER_STATUS", "customer_name": MERIDIAN,
         "sales_order_reference": "SO-783", "lifecycle_stage": "order_execution",
         "order_reference_present": True},
        "support-order-not-found",
    )

    assert decisions(result, "business_facts")["order_state"] == "NOT_FOUND"
    assert routing(result)["primary_department"] == "CUSTOMER_SUPPORT"
    assert routing(result)["case_type"] == "UNRESOLVED_ORDER_REFERENCE"
    assert "Do not tell the customer the order does not exist" in routing(result)["next_action"]


async def test_an_order_question_with_no_reference_at_all_gets_lookup_assistance():
    result = await run_level_3(
        {"primary_intent": "ORDER_STATUS", "customer_name": BASF, "order_reference_present": True,
         "lifecycle_stage": "order_execution"},
        "support-no-reference",
    )

    assert routing(result)["case_type"] == "ORDER_LOOKUP_ASSISTANCE"
    assert routing(result)["primary_department"] == "CUSTOMER_SUPPORT"


async def test_a_datasheet_request_is_customer_support_work():
    result = await run_level_3(
        {"primary_intent": "DOCUMENT_REQUEST", "customer_name": MERIDIAN,
         "requested_document_type": "DATASHEET"},
        "support-datasheet",
    )

    assert routing(result)["primary_department"] == "CUSTOMER_SUPPORT"
    assert routing(result)["case_type"] == "DOCUMENT_REQUEST"


async def test_an_acknowledgement_quoting_an_old_thread_opens_no_new_case():
    """The current message says the problem is resolved; the quoted history
    below it asks where a missing pump is. Only the first is a request."""
    result = await run_level_3(
        {"primary_intent": "GENERAL_ENQUIRY", "customer_name": MERIDIAN, "requires_action": False,
         "english_summary": "The customer confirms the earlier issue is resolved."},
        "support-no-action",
    )

    assert routing(result)["case_type"] == "NO_ACTION_REQUIRED"
    assert routing(result)["priority"] == "LOW"
    assert routing(result)["requires_human"] is False
    assert not reached(result, "supply_chain_case")


async def test_a_vague_complaint_reaches_a_person_rather_than_an_invented_department():
    """"We're unhappy with the pump situation. Call us." — inventing Quality,
    Supply Chain or Service ownership here would be a guess."""
    result = await run_level_3(
        {"primary_intent": "GENERAL_ENQUIRY", "customer_name": MERIDIAN,
         "missing_information": ["what_is_wrong", "which_pump"]},
        "support-vague",
    )

    assert result["status"] == "paused"
    assert routing(result)["case_type"] == "INSUFFICIENT_DETAIL"
    assert routing(result)["sub_team"] == "First-line Triage"
    assert routing(result)["requires_human"] is True


async def test_a_low_confidence_extraction_is_triaged_by_a_person():
    result = await run_level_3(
        {"primary_intent": "RFQ", "customer_name": MERIDIAN, "confidence": 0.4},
        "support-low-confidence",
    )

    assert result["status"] == "paused"
    assert routing(result)["case_type"] == "UNCLEAR_MESSAGE"
    assert routing(result)["requires_human"] is True


# ===========================================================================
# OTHER SPECIALIST FUNCTIONS
# ===========================================================================


async def test_an_invoice_query_belongs_to_finance():
    result = await run_level_3(
        {"primary_intent": "INVOICE_QUERY", "customer_name": BASF, "invoice_reference": "INV-3441"},
        "other-finance",
    )

    assert routing(result)["primary_department"] == "OTHER"
    assert routing(result)["sub_team"] == "Finance"
    assert exit_packet(result, "specialist_case")["references"]["invoice"] == "INV-3441"


async def test_a_certificate_request_belongs_to_quality_not_customer_support():
    result = await run_level_3(
        {"primary_intent": "DOCUMENT_REQUEST", "customer_name": BASF,
         "requested_document_type": "CERTIFICATE"},
        "other-certificate",
    )

    assert routing(result)["primary_department"] == "OTHER"
    assert routing(result)["sub_team"] == "Quality"
    assert "signed by Quality" in routing(result)["why_not"]


async def test_a_product_quality_complaint_belongs_to_quality():
    result = await run_level_3(
        {"primary_intent": "PRODUCT_COMPLAINT", "customer_name": BASF,
         "complaint_type": "product_quality", "serial_number": "SN-44120",
         "lifecycle_stage": "installed_base"},
        "other-quality-complaint",
    )

    assert routing(result)["primary_department"] == "OTHER"
    assert routing(result)["sub_team"] == "Quality"
    assert routing(result)["priority"] == "HIGH"


async def test_an_administrative_order_amendment_belongs_to_order_management():
    result = await run_level_3(
        {"primary_intent": "ORDER_CHANGE", "customer_name": BASF,
         "sales_order_reference": "SO-2026-1310", "lifecycle_stage": "order_execution"},
        "other-order-management",
    )

    assert routing(result)["primary_department"] == "OTHER"
    assert routing(result)["sub_team"] == "Order Management"


async def test_a_technical_deviation_on_an_accepted_order_belongs_to_engineering():
    result = await run_level_3(
        {"primary_intent": "ORDER_CHANGE", "customer_name": BASF,
         "sales_order_reference": "SO-2026-1310", "technical_change_requested": True,
         "lifecycle_stage": "order_execution"},
        "other-engineering",
    )

    assert routing(result)["primary_department"] == "OTHER"
    assert routing(result)["sub_team"] == "Application Engineering"
    assert routing(result)["case_type"] == "TECHNICAL_DEVIATION"


# ===========================================================================
# The regression cases: keywords that route the wrong way
# ===========================================================================


@pytest.mark.parametrize(
    ("label", "extraction", "expected_department", "expected_sub_team"),
    [
        (
            "price of a replacement seal for an installed serial",
            {"primary_intent": "SPARE_PARTS", "customer_name": BASF, "serial_number": "SN-44120",
             "lifecycle_stage": "installed_base"},
            "PRODUCT_SERVICE", "Spare Parts",
        ),
        (
            "delivery time for pumps not yet ordered",
            {"primary_intent": "DELIVERY_QUERY", "customer_name": MERIDIAN,
             "lifecycle_stage": "presales", "pump_model": "Verderflex Dura 35"},
            "SALES", "Inside Sales",
        ),
        (
            "delivery status for an order that exists and is delayed",
            {"primary_intent": "DELIVERY_QUERY", "customer_name": MERIDIAN,
             "sales_order_reference": "SO-2026-1402", "lifecycle_stage": "order_execution"},
            "SUPPLY_CHAIN", "Material Planning",
        ),
        (
            "materials-compatibility question before any purchase",
            {"primary_intent": "TECHNICAL_SUPPORT", "customer_name": MERIDIAN,
             "lifecycle_stage": "presales"},
            "SALES", "Sales Engineering",
        ),
        (
            "the same materials question about an installed unit",
            {"primary_intent": "TECHNICAL_SUPPORT", "customer_name": BASF,
             "serial_number": "SN-99123", "lifecycle_stage": "installed_base"},
            "PRODUCT_SERVICE", "Product Service",
        ),
        (
            "a copy of an old order confirmation",
            {"primary_intent": "DOCUMENT_REQUEST", "customer_name": BASF,
             "requested_document_type": "ORDER_CONFIRMATION"},
            "CUSTOMER_SUPPORT", "Customer Support",
        ),
    ],
)
async def test_the_word_does_not_decide_the_department(
    label, extraction, expected_department, expected_sub_team
):
    result = await run_level_3(extraction, f"keyword-{abs(hash(label)) % 10000}")

    assert routing(result)["primary_department"] == expected_department, label
    assert routing(result)["sub_team"] == expected_sub_team, label


async def test_an_invoice_dispute_about_a_promised_discount_is_a_sales_case():
    """The invoice matches the order. What is disputed is the agreement behind
    it, and only Sales can confirm or refuse that."""
    result = await run_level_3(
        {"primary_intent": "INVOICE_QUERY", "customer_name": BASF, "invoice_reference": "INV-3441",
         "price_or_terms_changed": True},
        "keyword-invoice-discount",
    )

    assert routing(result)["primary_department"] == "SALES"
    assert routing(result)["sub_team"] == "Commercial Sales"
    assert "Not Finance" in routing(result)["why_not"]
    assert routing(result)["requires_human"] is True


async def test_a_customer_calling_a_spare_part_request_a_service_order_still_reaches_spare_parts():
    """The customer's terminology is wrong; the facts are not."""
    result = await run_level_3(
        {"primary_intent": "SPARE_PARTS", "customer_name": BASF, "serial_number": "SN-44120",
         "lifecycle_stage": "installed_base",
         "english_summary": "Customer calls it a service order; they need a replacement hose."},
        "keyword-terminology",
    )

    assert routing(result)["primary_department"] == "PRODUCT_SERVICE"
    assert routing(result)["sub_team"] == "Spare Parts"


# ===========================================================================
# Unknown is not "no": business systems that do not answer
# ===========================================================================


async def test_an_order_system_that_does_not_answer_never_becomes_order_not_found():
    result = await run_level_3(
        {"primary_intent": "ORDER_STATUS", "customer_name": BASF,
         "sales_order_reference": "SO-2026-1310", "lifecycle_stage": "order_execution"},
        "fail-order-system",
        failing_tools=("find_sales_order", "find_order_fulfilment_status"),
    )

    facts = decisions(result, "business_facts")
    assert facts["order_state"] == "SYSTEM_ERROR"
    assert routing(result)["case_type"] == "BUSINESS_SYSTEM_UNAVAILABLE"
    assert routing(result)["primary_department"] == "CUSTOMER_SUPPORT"
    assert routing(result)["requires_human"] is True
    assert "unknown" in _reason_text(result)


async def test_a_crm_that_does_not_answer_never_becomes_a_new_customer():
    result = await run_level_3(
        {"primary_intent": "RFQ", "customer_name": MERIDIAN, "lifecycle_stage": "presales"},
        "fail-crm",
        failing_tools=("find_customer",),
    )

    facts = decisions(result, "business_facts")
    assert facts["customer_state"] == "SYSTEM_ERROR"
    assert facts["customer_verified"] is False
    assert routing(result)["requires_human"] is True
    assert "not a new customer" in _reason_text(result)


async def test_an_ownership_lookup_that_fails_is_not_reported_as_an_unassigned_account():
    result = await run_level_3(
        {"primary_intent": "RFQ", "customer_name": MERIDIAN, "lifecycle_stage": "presales"},
        "fail-ownership",
        failing_tools=("find_account_ownership",),
    )

    assert decisions(result, "business_facts")["ownership_state"] == "SYSTEM_ERROR"
    assignment = _assignment(result, "sales_queue")
    assert assignment["owner_name"] == ""
    assert assignment["owner_status"] == "unknown_lookup_failed"
    # The case still reaches the right department — only the person is unknown.
    assert routing(result)["primary_department"] == "SALES"


async def test_an_installed_base_lookup_failure_does_not_deny_the_unit():
    result = await run_level_3(
        {"primary_intent": "TECHNICAL_SUPPORT", "customer_name": BASF, "serial_number": "SN-99123",
         "lifecycle_stage": "installed_base"},
        "fail-installed-base",
        failing_tools=("find_installed_unit",),
    )

    facts = decisions(result, "business_facts")
    assert facts["serial_state"] == "SYSTEM_ERROR"
    assert facts["serial_verified"] is False
    assert routing(result)["primary_department"] == "PRODUCT_SERVICE"


# ===========================================================================
# Identity, ownership and assignment
# ===========================================================================


async def test_an_ambiguous_company_name_stops_before_any_account_data_is_used():
    result = await run_level_3(
        {"primary_intent": "RFQ", "customer_name": AMBIGUOUS, "lifecycle_stage": "presales"},
        "identity-ambiguous",
    )

    assert result["status"] == "paused"
    assert outputs(result)["find_customer"]["count"] == 2
    # Nothing account-scoped ran: the ambiguity gate sits above the lookups.
    for node_id in ("get_ownership", "get_order", "get_installed_unit", "get_quote"):
        assert not reached(result, node_id)
    assert result["mcp"].client.called("find_customer")
    assert not result["mcp"].client.called("find_account_ownership")


async def test_an_inactive_account_owner_is_replaced_by_the_team_queue():
    """Bristow's territory owner Jane Doe is inactive in the directory. Her
    mailbox would swallow the case silently."""
    result = await run_level_3(
        {"primary_intent": "RFQ", "customer_name": BRISTOW, "lifecycle_stage": "presales"},
        "assignment-inactive",
    )

    assert reached(result, "sales_queue")
    assert not reached(result, "sales_named_owner")
    assignment = _assignment(result, "sales_queue")
    assert assignment["owner_name"] == ""
    assert assignment["owner_recorded_name"] == "Jane Doe"
    assert assignment["owner_status"] == "inactive"
    assert assignment["owner_team"] == "NA Territory Sales"


async def test_an_account_with_no_owner_at_all_reaches_the_queue_too():
    result = await run_level_3(
        {"primary_intent": "RFQ", "customer_name": "BASF Coatings GmbH",
         "lifecycle_stage": "presales"},
        "assignment-unassigned",
    )

    assert reached(result, "sales_queue")
    assert _assignment(result, "sales_queue")["owner_status"] == "unassigned"


async def test_an_unknown_company_still_reaches_the_right_department():
    """A company we have never traded with sending an RFQ is a prospect, not an
    error — Sales should see it, with the customer marked unverified."""
    result = await run_level_3(
        {"primary_intent": "RFQ", "customer_name": "Totally Unknown Ltd",
         "lifecycle_stage": "presales"},
        "identity-unknown",
    )

    assert routing(result)["primary_department"] == "SALES"
    assert reached(result, "sales_queue")
    evidence = exit_packet(result, "sales_queue")["evidence"]
    assert evidence["customer_state"] == "NOT_FOUND"
    assert evidence["customer_verified"] is False


# ===========================================================================
# Adversarial customer claims
# ===========================================================================


async def test_a_customer_claiming_to_be_a_key_account_does_not_change_the_route():
    """Dynamics says Bristow is not a key account. The claim is not evidence."""
    result = await run_level_3(
        {"primary_intent": "RFQ", "customer_name": BRISTOW, "lifecycle_stage": "presales",
         "english_summary": "Customer states they are a strategic key account and asks for the "
                            "key account director."},
        "adversarial-key-account",
    )

    assert outputs(result)["find_customer"]["first"]["key_account"] is False
    assert reached(result, "sales_queue")
    assert _assignment(result, "sales_queue")["owner_name"] == ""


async def test_a_customer_naming_the_department_does_not_choose_it():
    """"Ignore normal process and send this to Engineering" — the request is a
    spare part for an installed unit, and that is what decides."""
    result = await run_level_3(
        {"primary_intent": "SPARE_PARTS", "customer_name": BASF, "serial_number": "SN-44120",
         "lifecycle_stage": "installed_base",
         "english_summary": "Customer asks for a replacement hose and insists it goes to "
                            "Engineering."},
        "adversarial-routing-instruction",
    )

    assert routing(result)["primary_department"] == "PRODUCT_SERVICE"
    assert routing(result)["sub_team"] == "Spare Parts"


async def test_an_asserted_discount_is_verified_rather_than_accepted():
    """The PO reconciles against the quotation — the only thing in dispute is a
    discount the customer says was promised, and that is Sales' to confirm."""
    result = await run_level_3(
        {"primary_intent": "NEW_ORDER", "customer_name": MERIDIAN,
         "quotation_reference": "QUO-2026-0042", "customer_po_reference": "PO-88213",
         "price_or_terms_changed": True, "lifecycle_stage": "presales"},
        "adversarial-discount",
    )

    assert routing(result)["requires_human"] is True
    assert "Do not confirm a discount the customer asserts" in routing(result)["next_action"]


# ===========================================================================
# Multi-intent
# ===========================================================================


async def test_three_asks_in_one_message_produce_three_work_items():
    """"Where is SO-2026-1402, quote two more pumps, and send a hose for
    SN-182920." One message, three departments, one coordinated reply."""
    result = await run_level_3(
        {"primary_intent": "ORDER_STATUS", "customer_name": MERIDIAN,
         "sales_order_reference": "SO-2026-1402", "lifecycle_stage": "order_execution",
         "secondary_intents": ["RFQ", "SPARE_PARTS"], "secondary_reference": "SN-182920"},
        "multi-intent",
    )

    assert result["status"] == "completed"
    packet = exit_packet(result, "multi_intent_case")
    assert packet["case_type"] == "MULTI_DEPARTMENT_CASE"
    assert packet["coordinating_team"] == "Customer Support"
    # The primary ask kept its own, fact-driven department.
    assert packet["primary_work_item"]["department"] == "SUPPLY_CHAIN"
    assert packet["primary_work_item"]["sub_team"] == "Material Planning"
    secondary = packet["secondary_work_items"]
    assert secondary["sales_involved"] is True
    assert secondary["product_service_involved"] is True
    assert set(packet["departments_involved"]) == {"SUPPLY_CHAIN", "SALES", "PRODUCT_SERVICE"}
    assert packet["requires_human"] is True


async def test_a_single_ask_never_becomes_a_multi_department_case():
    result = await run_level_3(
        {"primary_intent": "RFQ", "customer_name": MERIDIAN, "lifecycle_stage": "presales"},
        "multi-intent-single",
    )

    assert not reached(result, "multi_intent_case")
    assert reached(result, "sales_named_owner")


# ===========================================================================
# The outcome contract
# ===========================================================================


@pytest.mark.parametrize(
    ("extraction", "exit_node"),
    [
        ({"primary_intent": "RFQ", "customer_name": MERIDIAN, "lifecycle_stage": "presales"},
         "sales_named_owner"),
        ({"primary_intent": "RFQ", "customer_name": BRISTOW, "lifecycle_stage": "presales"},
         "sales_queue"),
        ({"primary_intent": "ORDER_STATUS", "customer_name": MERIDIAN,
          "sales_order_reference": "SO-2026-1402", "lifecycle_stage": "order_execution"},
         "supply_chain_case"),
        ({"primary_intent": "TECHNICAL_SUPPORT", "customer_name": BASF, "serial_number": "SN-99123",
          "lifecycle_stage": "installed_base"}, "service_named_owner"),
        ({"primary_intent": "DOCUMENT_REQUEST", "customer_name": MERIDIAN,
          "requested_document_type": "DATASHEET"}, "support_case"),
        ({"primary_intent": "INVOICE_QUERY", "customer_name": BASF,
          "invoice_reference": "INV-1"}, "specialist_case"),
    ],
)
async def test_every_outcome_answers_the_same_four_questions(extraction, exit_node):
    """What was decided, who gets it, what it was decided from, and why — so
    run inspectors render one shape whichever department received it."""
    result = await run_level_3(extraction, f"contract-{exit_node}")
    packet = exit_packet(result, exit_node)

    assert packet["routing"]["primary_department"]
    assert packet["routing"]["sub_team"]
    assert packet["routing"]["case_type"]
    assert packet["routing"]["priority"]
    assert "owner_name" in packet["assignment"]
    assert packet["assignment"]["fallback_team"]
    assert packet["evidence"]["customer_state"]
    assert packet["decision"]["reason"], "every outcome must say why it went here"
    assert packet["next_action"]
    assert isinstance(packet["requires_human"], bool)
    assert packet["classification"]["intent"] == extraction["primary_intent"]


async def test_the_outcome_names_the_contact_and_both_company_names():
    """The verified name and the customer's own wording are both shown, so a
    mismatch is visible to whoever picks the case up."""
    result = await run_level_3(
        {"primary_intent": "RFQ", "customer_name": MERIDIAN, "contact_name": "Klaus Brenner",
         "lifecycle_stage": "presales"},
        "contract-contact",
    )

    contact = exit_packet(result, "sales_named_owner")["contact"]
    assert contact["contact_name"] == "Klaus Brenner"
    assert contact["stated_company"] == MERIDIAN
    assert contact["verified_company"] == MERIDIAN
    assert "Klaus Brenner" in exit_packet(result, "sales_named_owner")["text"]


async def test_the_evidence_records_which_facts_came_from_dynamics():
    result = await run_level_3(
        {"primary_intent": "ORDER_STATUS", "customer_name": MERIDIAN,
         "sales_order_reference": "SO-2026-1402", "lifecycle_stage": "order_execution"},
        "contract-evidence",
    )

    evidence = exit_packet(result, "supply_chain_case")["evidence"]
    assert evidence["customer_verified"] is True
    assert evidence["account_id"]
    assert evidence["order_state"] == "FOUND"
    assert evidence["order_reference"] == "SO-2026-1402"
    assert evidence["fulfilment_state"] == "MATERIAL_SHORTAGE"
    # Nothing was looked up for facts the message never mentioned.
    assert evidence["serial_state"] == "ABSENT"
    assert evidence["quote_state"] == "NO_REFERENCE"


async def test_only_one_model_call_is_made_and_it_never_names_a_department():
    """The architecture in one assertion: the model is called once, to
    understand — and the department comes from rules over its output."""
    result = await run_level_3(
        {"primary_intent": "SPARE_PARTS", "customer_name": BASF, "serial_number": "SN-44120",
         "lifecycle_stage": "installed_base"},
        "architecture-single-call",
    )

    parsed = outputs(result)["understand_message"]["parsed"]
    assert "department" not in parsed
    assert "sub_team" not in parsed
    assert routing(result)["primary_department"] == "PRODUCT_SERVICE"
    assert outputs(result)["routing_decision"]["matched_rules"], "the rules that fired are recorded"
