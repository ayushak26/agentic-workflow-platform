"""The deterministic rule engine.

Two things are being protected here. The first is correctness of each operator,
including the awkward cases real extracted data produces (a boolean that arrived
as the string "true", a number that arrived as "15 m³/h"). The second — and the
reason this engine exists rather than a prompt — is that every evaluation
explains itself, so a business decision can be shown rather than trusted.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.runtime.rules import (
    OPERATORS_BY_TYPE,
    Action,
    Condition,
    ConditionGroup,
    Rule,
    evaluate_condition,
    evaluate_group,
    evaluate_rules,
    operators_for_type,
    path_exists,
    resolve_path,
)


def state(result: dict) -> dict:
    return {"node_outputs": {"extract": {"result": result, "confidence": 0.9}}}


def check(field: str, operator: str, value=None, *, result: dict) -> bool:
    return evaluate_condition(
        Condition(field=field, operator=operator, value=value), state(result)
    ).matched


class TestPathResolution:
    def test_outputs_prefix_resolves(self):
        assert (
            resolve_path(state({"intent": "sales"}), "outputs.extract.result.intent")
            == "sales"
        )

    def test_bare_node_id_shorthand_resolves(self):
        assert (
            resolve_path(state({"intent": "sales"}), "extract.result.intent")
            == "sales"
        )

    def test_missing_path_returns_none_without_raising(self):
        """A rule asking "does this field exist" must be able to look at a path
        that isn't there. The template resolver raises; this one cannot."""
        assert resolve_path(state({}), "outputs.extract.result.nope") is None
        assert path_exists(state({}), "outputs.extract.result.nope") is False

    def test_items_addresses_the_element_shape_of_a_list(self):
        resolved = resolve_path(
            state({"line_items": [{"part": "a"}, {"part": "b"}]}),
            "outputs.extract.result.line_items.items.part",
        )
        assert resolved == ["a", "b"]


class TestComparisonOperators:
    def test_string_equality_ignores_case_and_padding(self):
        assert check(
            "outputs.extract.result.intent",
            "equals",
            "Technical_Support",
            result={"intent": " technical_support "},
        )

    def test_numeric_comparison_accepts_a_plain_numeric_string(self):
        """Workflow inputs are untyped text, so "15" must compare as 15."""
        assert check(
            "outputs.extract.result.flow",
            "greater_or_equal",
            15,
            result={"flow": "15"},
        )

    def test_numeric_comparison_refuses_to_guess_at_a_unit_bearing_string(self):
        """Deliberately strict, and safe to be strict about.

        A schema field typed `number` is already a float by the time a rule sees
        it (the AI Task validates through the compiled model), so "15 m³/h" can
        only reach here from a *string* field — and preflight rejects numeric
        operators on a string field with RULE_TYPE_MISMATCH. Guessing "15" out of
        the text would instead silently disagree with "approx. 20-30", where the
        first number is not the one the author meant. Use a Transform's `number`
        operation to parse it explicitly.
        """
        assert not check(
            "outputs.extract.result.flow",
            "greater_or_equal",
            15,
            result={"flow": "15 m3/h"},
        )

    def test_boolean_matches_a_boolean_looking_string(self):
        assert check(
            "outputs.extract.result.stopped",
            "is_true",
            result={"stopped": "true"},
        )

    def test_boolean_never_coerces_an_unrelated_string(self):
        """`true == "critical"` must be False, not truthiness agreement."""
        assert not check(
            "outputs.extract.result.stopped",
            "equals",
            "critical",
            result={"stopped": True},
        )

    def test_contains_matches_a_list_member_case_insensitively(self):
        assert check(
            "outputs.extract.result.missing",
            "contains",
            "product_model",
            result={"missing": ["Product_Model"]},
        )

    def test_in_checks_membership_of_alternatives(self):
        assert check(
            "outputs.extract.result.intent",
            "in",
            ["complaint", "technical_support"],
            result={"intent": "complaint"},
        )


class TestMissingValues:
    def test_is_empty_matches_blank_string_and_missing_path(self):
        assert check("outputs.extract.result.model", "is_empty", result={"model": " "})
        assert check("outputs.extract.result.model", "is_empty", result={})

    def test_exists_requires_a_non_null_value(self):
        assert not check("outputs.extract.result.model", "exists", result={"model": None})
        assert check("outputs.extract.result.model", "exists", result={"model": "Dura 15"})

    def test_negative_operators_do_not_pass_on_a_missing_value(self):
        """The subtle one. If `not_equals` passed for data that was never
        extracted, an incomplete request would route as though it were complete —
        so a missing path fails every value comparison, negative ones included."""
        assert not check(
            "outputs.extract.result.intent", "not_equals", "complaint", result={}
        )
        assert not check(
            "outputs.extract.result.missing", "not_contains", "model", result={}
        )

    def test_a_trace_reports_missing_separately_from_mismatched(self):
        trace = evaluate_condition(
            Condition(
                field="outputs.extract.result.intent",
                operator="equals",
                value="complaint",
            ),
            state({}),
        )
        assert trace.missing is True
        assert "not present" in trace.summary


class TestNestedGroups:
    def test_or_of_a_signal_and_a_nested_and(self):
        group = ConditionGroup(
            operator="or",
            conditions=[
                Condition(
                    field="outputs.extract.result.production_stopped",
                    operator="is_true",
                ),
                ConditionGroup(
                    operator="and",
                    conditions=[
                        Condition(
                            field="outputs.extract.result.urgency",
                            operator="equals",
                            value="high",
                        ),
                        Condition(
                            field="outputs.extract.result.tier",
                            operator="equals",
                            value="strategic",
                        ),
                    ],
                ),
            ],
        )
        matched = evaluate_group(
            group,
            state(
                {
                    "production_stopped": False,
                    "urgency": "high",
                    "tier": "strategic",
                }
            ),
        )
        assert matched.matched is True

        not_matched = evaluate_group(
            group,
            state({"production_stopped": False, "urgency": "high", "tier": "small"}),
        )
        assert not_matched.matched is False

    def test_not_group_inverts_its_single_child(self):
        group = ConditionGroup(
            operator="not",
            conditions=[
                Condition(
                    field="outputs.extract.result.intent",
                    operator="equals",
                    value="complaint",
                )
            ],
        )
        assert evaluate_group(group, state({"intent": "sales"})).matched is True

    def test_not_group_must_have_exactly_one_child(self):
        with pytest.raises(ValidationError, match="exactly one"):
            ConditionGroup(
                operator="not",
                conditions=[
                    Condition(field="a", operator="exists"),
                    Condition(field="b", operator="exists"),
                ],
            )

    def test_group_with_no_conditions_is_rejected(self):
        with pytest.raises(ValidationError, match="at least one"):
            ConditionGroup(operator="and", conditions=[])

    def test_nested_group_survives_the_round_trip_through_json(self):
        """The editor stores groups as JSON in the workflow YAML, so a group must
        not be silently re-parsed as a leaf condition."""
        original = ConditionGroup(
            operator="or",
            conditions=[
                Condition(field="a", operator="exists"),
                ConditionGroup(
                    operator="and",
                    conditions=[Condition(field="b", operator="is_true")],
                ),
            ],
        )
        restored = ConditionGroup.model_validate(original.model_dump())
        assert isinstance(restored.conditions[1], ConditionGroup)


class TestRuleEvaluation:
    def test_all_matching_rules_apply_not_just_the_first(self):
        """Escalation reasons are additive: two independent reasons should both
        appear, which first-match-wins would hide."""
        evaluation = evaluate_rules(
            [
                Rule(
                    name="low confidence",
                    when=ConditionGroup(
                        conditions=[
                            Condition(
                                field="outputs.extract.confidence",
                                operator="less_than",
                                value=0.95,
                            )
                        ]
                    ),
                    then=[Action(field="human_review", value=True)],
                ),
                Rule(
                    name="complaint",
                    when=ConditionGroup(
                        conditions=[
                            Condition(
                                field="outputs.extract.result.intent",
                                operator="equals",
                                value="complaint",
                            )
                        ]
                    ),
                    then=[Action(field="escalated", value=True)],
                ),
            ],
            state({"intent": "complaint"}),
        )
        assert evaluation.matched_rules == ["low confidence", "complaint"]
        assert evaluation.values == {"human_review": True, "escalated": True}

    def test_stop_on_match_short_circuits(self):
        evaluation = evaluate_rules(
            [
                Rule(
                    name="first",
                    when=ConditionGroup(
                        conditions=[Condition(field="outputs.extract", operator="exists")]
                    ),
                    then=[Action(field="a", value=1)],
                    stop_on_match=True,
                ),
                Rule(name="second", default=True, then=[Action(field="b", value=2)]),
            ],
            state({}),
        )
        assert evaluation.values == {"a": 1}

    def test_a_later_rule_can_read_an_earlier_rule_s_conclusion(self):
        evaluation = evaluate_rules(
            [
                Rule(
                    name="derive urgency",
                    when=ConditionGroup(
                        conditions=[
                            Condition(
                                field="outputs.extract.result.stopped",
                                operator="is_true",
                            )
                        ]
                    ),
                    then=[Action(field="urgency", value="critical")],
                ),
                Rule(
                    name="notify on critical",
                    when=ConditionGroup(
                        conditions=[
                            Condition(
                                field="decisions.urgency",
                                operator="equals",
                                value="critical",
                            )
                        ]
                    ),
                    then=[Action(field="notify", value=True)],
                ),
            ],
            state({"stopped": True}),
        )
        assert evaluation.values["notify"] is True

    def test_defaults_seed_the_values_before_any_rule_runs(self):
        evaluation = evaluate_rules(
            [],
            state({}),
            initial={"human_review": False},
        )
        assert evaluation.values == {"human_review": False}

    def test_action_can_copy_a_live_value_with_the_dollar_prefix(self):
        evaluation = evaluate_rules(
            [
                Rule(
                    name="carry intent",
                    default=True,
                    then=[
                        Action(field="route", value="$outputs.extract.result.intent")
                    ],
                )
            ],
            state({"intent": "sales"}),
        )
        assert evaluation.values["route"] == "sales"

    def test_append_and_increase_operations(self):
        evaluation = evaluate_rules(
            [
                Rule(
                    name="collect",
                    default=True,
                    then=[
                        Action(field="reasons", operation="append", value="one"),
                        Action(field="reasons", operation="append", value="two"),
                        Action(field="score", operation="increase", value=2),
                    ],
                )
            ],
            state({}),
        )
        assert evaluation.values["reasons"] == ["one", "two"]
        assert evaluation.values["score"] == 2

    def test_rule_without_conditions_or_default_is_rejected(self):
        with pytest.raises(ValidationError, match="default: true"):
            Rule(name="broken", then=[Action(field="a", value=1)])

    def test_rule_without_actions_is_rejected(self):
        with pytest.raises(ValidationError, match="no actions"):
            Rule(name="broken", default=True)


class TestExplainability:
    def test_explanation_names_the_rule_the_conditions_and_the_result(self):
        """This output is what the Builder's "why did this branch run?" panel
        renders — the reason it is asserted here rather than treated as debug
        output."""
        evaluation = evaluate_rules(
            [
                Rule(
                    name="Priority support",
                    when=ConditionGroup(
                        operator="and",
                        conditions=[
                            Condition(
                                field="outputs.extract.result.intent",
                                operator="equals",
                                value="technical_support",
                            ),
                            Condition(
                                field="outputs.extract.confidence",
                                operator="greater_or_equal",
                                value=0.8,
                            ),
                        ],
                    ),
                    then=[Action(field="route", value="priority_support")],
                )
            ],
            state({"intent": "technical_support"}),
        )
        lines = evaluation.explanation_lines()
        assert lines[0] == "Priority support (matched)"
        assert any("intent equals 'technical_support'" in line for line in lines)
        assert any("confidence is at least 0.8" in line for line in lines)
        assert any("route = 'priority_support'" in line for line in lines)

    def test_non_matching_rules_are_traced_too(self):
        evaluation = evaluate_rules(
            [
                Rule(
                    name="never",
                    when=ConditionGroup(
                        conditions=[
                            Condition(
                                field="outputs.extract.result.intent",
                                operator="equals",
                                value="complaint",
                            )
                        ]
                    ),
                    then=[Action(field="a", value=1)],
                )
            ],
            state({"intent": "sales"}),
        )
        trace = evaluation.rules[0]
        assert trace.matched is False
        leaf = trace.trace.children[0]
        assert leaf.actual == "sales"
        assert "actual 'sales'" in leaf.summary

    def test_long_values_are_trimmed_out_of_traces(self):
        """Traces travel over SSE and land in run history; an extracted document
        body must not ride along."""
        trace = evaluate_condition(
            Condition(
                field="outputs.extract.result.body", operator="equals", value="x"
            ),
            state({"body": "y" * 5000}),
        )
        assert len(str(trace.actual)) < 300


class TestTypedOperatorCatalog:
    def test_a_list_is_not_offered_a_numeric_operator(self):
        """The editor and preflight read this same table, which is what stops the
        editor from building a rule the validator then rejects."""
        assert "greater_or_equal" not in operators_for_type("list")
        assert "contains" in operators_for_type("list")

    def test_a_boolean_is_offered_true_false(self):
        assert "is_true" in operators_for_type("boolean")

    def test_unknown_types_fall_back_to_everything(self):
        assert len(operators_for_type("something_else")) > 5

    def test_every_declared_type_has_operators(self):
        for field_type, operators in OPERATORS_BY_TYPE.items():
            assert operators, f"{field_type} has no operators"


class TestConditionValidation:
    def test_set_operators_need_a_non_empty_list(self):
        with pytest.raises(ValidationError, match="alternatives"):
            Condition(field="a", operator="in", value="not-a-list")

    def test_binary_operators_need_a_value(self):
        with pytest.raises(ValidationError, match="needs a value"):
            Condition(field="a", operator="equals")

    def test_unary_operators_need_no_value(self):
        assert Condition(field="a", operator="is_empty").value is None
