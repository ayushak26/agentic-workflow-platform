"""Routing tests for the two demonstration levels, and for the progression.

Level 1  understands the message and routes on rules alone.
Level 2  adds Dynamics 365, and the same sentence starts routing differently.
Level 3  (tests/test_pump_manufacturer_case_routing.py) adds the messy cases.

The progression tests at the bottom are the point of having three files rather
than one: they assert that each level really is a superset of the one before —
same departments, more evidence — because a demo that quietly changes the model
between slides teaches the audience nothing.
"""
from __future__ import annotations

import pytest

from tests.helpers.pump_routing import (
    AMBIGUOUS,
    BASF,
    BRISTOW,
    LEVEL_1,
    LEVEL_2,
    LEVEL_3,
    MERIDIAN,
    decisions,
    exit_packet,
    outputs,
    reached,
    run_level_1,
    run_level_2,
    run_level_3,
)

DEPARTMENTS = {
    "SALES",
    "SUPPLY_CHAIN",
    "PRODUCT_SERVICE_SPARE_PARTS",
    "CUSTOMER_SUPPORT",
    "OTHER",
}


# ===========================================================================
# LEVEL 1 — rules only, no business systems
# ===========================================================================


@pytest.mark.parametrize(
    ("label", "extraction", "department", "exit_node"),
    [
        ("quote five pumps", {"intent": "QUOTATION", "lifecycle_stage": "presales"},
         "SALES", "sales_case"),
        ("which pump for this duty", {"intent": "NEW_PRODUCT_ENQUIRY", "lifecycle_stage": "presales"},
         "SALES", "sales_case"),
        ("where is SO-12345", {"intent": "ORDER_OR_DELIVERY", "order_reference": "SO-12345",
                               "lifecycle_stage": "order_execution"},
         "SUPPLY_CHAIN", "supply_chain_case"),
        ("our installed pump is failing", {"intent": "TECHNICAL_SUPPORT",
                                           "lifecycle_stage": "installed_base"},
         "PRODUCT_SERVICE_SPARE_PARTS", "product_service_case"),
        ("replacement hose for SN-88201", {"intent": "SPARE_PARTS", "serial_number": "SN-88201",
                                           "lifecycle_stage": "installed_base"},
         "PRODUCT_SERVICE_SPARE_PARTS", "product_service_case"),
        ("please send the manual", {"intent": "DOCUMENT_REQUEST"},
         "CUSTOMER_SUPPORT", "customer_support_case"),
        ("this invoice is wrong", {"intent": "INVOICE"},
         "OTHER", "other_case"),
    ],
)
async def test_level_1_routes_each_kind_of_request(label, extraction, department, exit_node):
    result = await run_level_1(extraction, f"l1-{abs(hash(label)) % 10000}")

    assert result["status"] == "completed", label
    assert decisions(result, "department_decision")["department"] == department, label
    assert reached(result, exit_node), label
    packet = exit_packet(result, exit_node)
    assert packet["reason"], "every Level 1 outcome states its reason"
    assert packet["next_action"]


async def test_level_1_sends_a_spare_part_price_to_service_not_to_sales():
    """The first edge case a business audience recognises: the word "price"
    does not make it a Sales enquiry."""
    result = await run_level_1(
        {"intent": "SPARE_PARTS", "lifecycle_stage": "installed_base",
         "summary": "Customer asks how much a replacement seal costs."},
        "l1-spare-price",
    )

    assert decisions(result, "department_decision")["department"] == "PRODUCT_SERVICE_SPARE_PARTS"


async def test_level_1_sends_a_lead_time_question_to_sales_because_no_order_exists():
    result = await run_level_1(
        {"intent": "ORDER_OR_DELIVERY", "lifecycle_stage": "presales"},
        "l1-lead-time",
    )

    assert decisions(result, "department_decision")["department"] == "SALES"
    assert decisions(result, "department_decision")["case_type"] == "LEAD_TIME_ENQUIRY"


async def test_level_1_sends_a_presales_technical_question_to_sales():
    result = await run_level_1(
        {"intent": "TECHNICAL_SUPPORT", "lifecycle_stage": "presales"},
        "l1-presales-technical",
    )

    assert decisions(result, "department_decision")["department"] == "SALES"
    assert decisions(result, "department_decision")["case_type"] == "PRESALES_TECHNICAL_QUESTION"


async def test_level_1_asks_customer_support_when_an_order_has_no_reference():
    result = await run_level_1(
        {"intent": "ORDER_OR_DELIVERY", "lifecycle_stage": "order_execution"},
        "l1-no-reference",
    )

    assert decisions(result, "department_decision")["department"] == "CUSTOMER_SUPPORT"
    assert decisions(result, "department_decision")["case_type"] == "ORDER_LOOKUP_ASSISTANCE"


async def test_level_1_needs_no_business_systems_at_all():
    """The demo point of Level 1: it runs with nothing connected."""
    assert not any(node.type == "MCPToolAgent" for node in LEVEL_1.nodes)


# ===========================================================================
# LEVEL 2 — the same sentence, decided by Dynamics 365
# ===========================================================================


async def test_level_2_routes_the_same_question_two_ways_depending_on_the_erp():
    """This is the Level 2 demonstration, as one test. "When will the pump
    arrive?" — with no order it is a Sales lead-time question; with SO-2026-1402
    and a material shortage behind it, it is Material Planning's."""
    no_order = await run_level_2(
        {"intent": "ORDER_OR_DELIVERY", "customer_name": MERIDIAN, "lifecycle_stage": "presales"},
        "l2-no-order",
    )
    with_order = await run_level_2(
        {"intent": "ORDER_OR_DELIVERY", "customer_name": MERIDIAN,
         "order_reference": "SO-2026-1402", "lifecycle_stage": "order_execution"},
        "l2-with-order",
    )

    assert decisions(no_order, "business_context")["order_exists"] is False
    assert decisions(no_order, "routing_decision")["department"] == "SALES"

    assert decisions(with_order, "business_context")["order_exists"] is True
    assert decisions(with_order, "business_context")["fulfilment_state"] == "MATERIAL_SHORTAGE"
    assert decisions(with_order, "routing_decision")["department"] == "SUPPLY_CHAIN"
    assert decisions(with_order, "routing_decision")["sub_team"] == "Material Planning"


async def test_level_2_names_the_account_owner_from_dynamics():
    result = await run_level_2(
        {"intent": "QUOTATION", "customer_name": MERIDIAN, "lifecycle_stage": "presales"},
        "l2-named-owner",
    )

    assert reached(result, "sales_named_owner")
    packet = exit_packet(result, "sales_named_owner")
    assert packet["owner_name"] == "Elena Cross"
    assert packet["business_context"]["customer_verified"] is True


async def test_level_2_falls_back_to_the_team_when_dynamics_names_nobody_assignable():
    result = await run_level_2(
        {"intent": "QUOTATION", "customer_name": BRISTOW, "lifecycle_stage": "presales"},
        "l2-queue",
    )

    assert reached(result, "sales_queue")
    assert exit_packet(result, "sales_queue")["owner_name"] == ""


async def test_level_2_sends_a_technical_rfq_to_the_sales_engineer():
    result = await run_level_2(
        {"intent": "QUOTATION", "customer_name": MERIDIAN, "technical_complexity": "technical",
         "lifecycle_stage": "presales"},
        "l2-technical",
    )

    assert decisions(result, "routing_decision")["sub_team"] == "Sales Engineering"
    assert exit_packet(result, "sales_named_owner")["owner_name"] == "Raj Patel"


async def test_level_2_sends_a_quality_hold_to_quality_not_to_supply_chain():
    result = await run_level_2(
        {"intent": "ORDER_OR_DELIVERY", "customer_name": BASF, "order_reference": "SO-2026-1455",
         "lifecycle_stage": "order_execution"},
        "l2-quality-hold",
    )

    assert decisions(result, "business_context")["fulfilment_state"] == "QUALITY_HOLD"
    assert decisions(result, "routing_decision")["department"] == "OTHER"
    assert decisions(result, "routing_decision")["sub_team"] == "Quality"


async def test_level_2_sends_a_dispatched_order_to_logistics_support():
    result = await run_level_2(
        {"intent": "ORDER_OR_DELIVERY", "customer_name": BASF, "order_reference": "SO-2025-0977",
         "lifecycle_stage": "order_execution"},
        "l2-dispatched",
    )

    assert decisions(result, "routing_decision")["department"] == "CUSTOMER_SUPPORT"
    assert decisions(result, "routing_decision")["sub_team"] == "Logistics Support"


async def test_level_2_verifies_the_serial_and_names_the_service_owner():
    result = await run_level_2(
        {"intent": "TECHNICAL_SUPPORT", "customer_name": BASF, "serial_number": "SN-99123",
         "lifecycle_stage": "installed_base"},
        "l2-service-named",
    )

    assert decisions(result, "business_context")["serial_verified"] is True
    packet = exit_packet(result, "service_named_owner")
    assert packet["owner_name"] == "Hans Vogel"
    assert packet["installed_unit"]["pump_model"] == "Verderflex Dura 85"


async def test_level_2_asks_for_the_serial_when_the_unit_cannot_be_identified():
    result = await run_level_2(
        {"intent": "SPARE_PARTS", "customer_name": BASF, "lifecycle_stage": "installed_base"},
        "l2-no-serial",
    )

    assert decisions(result, "routing_decision")["department"] == "CUSTOMER_SUPPORT"
    assert decisions(result, "routing_decision")["case_type"] == "UNIDENTIFIED_UNIT"


async def test_level_2_sends_a_spare_part_price_to_spare_parts():
    result = await run_level_2(
        {"intent": "SPARE_PARTS", "customer_name": BASF, "serial_number": "SN-44120",
         "lifecycle_stage": "installed_base"},
        "l2-spare-price",
    )

    assert decisions(result, "routing_decision")["department"] == "PRODUCT_SERVICE_SPARE_PARTS"
    assert decisions(result, "routing_decision")["sub_team"] == "Spare Parts"


async def test_level_2_sends_a_certificate_request_to_quality():
    result = await run_level_2(
        {"intent": "DOCUMENT_REQUEST", "customer_name": BASF,
         "requested_document_type": "CERTIFICATE"},
        "l2-certificate",
    )

    assert decisions(result, "routing_decision")["department"] == "OTHER"
    assert decisions(result, "routing_decision")["sub_team"] == "Quality"


async def test_level_2_asks_a_person_to_confirm_an_ambiguous_company():
    result = await run_level_2(
        {"intent": "QUOTATION", "customer_name": AMBIGUOUS, "lifecycle_stage": "presales"},
        "l2-ambiguous",
    )

    assert outputs(result)["find_customer"]["count"] == 2
    assert decisions(result, "business_context")["customer_ambiguous"] is True
    assert decisions(result, "routing_decision")["case_type"] == "CUSTOMER_IDENTITY_UNCONFIRMED"
    assert decisions(result, "routing_decision")["department"] == "CUSTOMER_SUPPORT"


# ===========================================================================
# The progression itself
# ===========================================================================


def test_all_three_levels_use_the_same_five_departments():
    """A demo that changes the model between slides teaches nothing. The
    department vocabulary is identical at every level; only the evidence
    behind the decision grows."""
    for spec in (LEVEL_1, LEVEL_2):
        router = next(node for node in spec.nodes if node.id == "department_router")
        assert set(router.config["branches"]) == DEPARTMENTS

    level_3_router = next(
        node for node in LEVEL_3.nodes if node.id == "primary_department_router"
    )
    # Level 3 shortens the service label; the five business functions are the same.
    assert set(level_3_router.config["branches"]) == {
        "SALES", "SUPPLY_CHAIN", "PRODUCT_SERVICE", "CUSTOMER_SUPPORT", "OTHER",
    }


def test_each_level_adds_capability_rather_than_replacing_it():
    def node_types(spec):
        return [node.type for node in spec.nodes]

    assert node_types(LEVEL_1).count("TransformAgent") == 1
    assert node_types(LEVEL_2).count("TransformAgent") == 1
    assert node_types(LEVEL_3).count("TransformAgent") == 1, (
        "one model call at every level — understanding, never routing"
    )

    lookups_1 = node_types(LEVEL_1).count("MCPToolAgent")
    lookups_2 = node_types(LEVEL_2).count("MCPToolAgent")
    lookups_3 = node_types(LEVEL_3).count("MCPToolAgent")
    assert lookups_1 == 0
    assert lookups_2 > lookups_1
    assert lookups_3 > lookups_2

    # Only Level 3 stops for a person.
    assert node_types(LEVEL_1).count("HumanInLoopAgent") == 0
    assert node_types(LEVEL_2).count("HumanInLoopAgent") == 0
    assert node_types(LEVEL_3).count("HumanInLoopAgent") == 4


def test_the_levels_grow_in_size_but_stay_readable():
    assert len(LEVEL_1.nodes) <= 15, "Level 1 must stay explainable in two minutes"
    assert len(LEVEL_1.nodes) < len(LEVEL_2.nodes) < len(LEVEL_3.nodes)
    assert len(LEVEL_3.nodes) <= 60, (
        "complexity must come from business decisions, not from a node per outcome"
    )


def test_no_level_lets_the_model_choose_the_department():
    """The one architectural rule that holds at every level: the extraction
    schema contains no department, team or owner field."""
    for spec in (LEVEL_1, LEVEL_2, LEVEL_3):
        understand = next(node for node in spec.nodes if node.id == "understand_message")
        fields = set(understand.config["output_schema"])
        assert not fields & {"department", "sub_team", "owner", "owner_name", "team", "route"}


@pytest.mark.parametrize(
    ("runner", "extraction", "run_id"),
    [
        (run_level_1, {"intent": "SPARE_PARTS", "lifecycle_stage": "installed_base"}, "prog-l1"),
        (run_level_2, {"intent": "SPARE_PARTS", "customer_name": BASF,
                       "serial_number": "SN-44120", "lifecycle_stage": "installed_base"}, "prog-l2"),
        (run_level_3, {"primary_intent": "SPARE_PARTS", "customer_name": BASF,
                       "serial_number": "SN-44120", "lifecycle_stage": "installed_base"}, "prog-l3"),
    ],
)
async def test_the_same_message_reaches_spare_parts_at_every_level(runner, extraction, run_id):
    """Same customer request, three levels, one answer — with more evidence
    behind it each time."""
    result = await runner(extraction, run_id)

    assert result["status"] == "completed"
    departments = {
        node_id: output.get("data", {}).get("department")
        for node_id, output in outputs(result).items()
        if isinstance(output, dict) and isinstance(output.get("data"), dict)
    }
    routed_to = {value for value in departments.values() if value}
    assert routed_to & {"PRODUCT_SERVICE_SPARE_PARTS"} or any(
        output.get("data", {}).get("routing", {}).get("primary_department") == "PRODUCT_SERVICE"
        for output in outputs(result).values()
        if isinstance(output, dict) and isinstance(output.get("data"), dict)
    )
