"""The visual schema builder's compiler.

These tests pin the property that makes the schema builder trustworthy: the
Pydantic model the response is validated against, the JSON Schema the provider is
given, and the dotted paths preflight authorises are all derived from the same
rows, so they cannot disagree.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.runtime.field_schema import (
    FieldSpec,
    build_response_model,
    describe_schema,
    field_paths,
    json_schema,
    parse_fields,
    validate_fields,
)


def rows(*items: dict) -> list[FieldSpec]:
    return parse_fields(list(items))


class TestScalarCompilation:
    def test_required_scalar_must_be_present(self):
        model = build_response_model(rows({"name": "language", "type": "string"}))
        assert model(language="de").language == "de"
        with pytest.raises(ValidationError):
            model()

    def test_enum_becomes_a_closed_literal(self):
        """The point of an enum is that a near-miss label is rejected, not
        silently accepted — that is what makes routing on it safe."""
        model = build_response_model(
            rows(
                {
                    "name": "intent",
                    "type": "enum",
                    "enum_values": ["technical_support", "other"],
                }
            )
        )
        assert model(intent="technical_support").intent == "technical_support"
        with pytest.raises(ValidationError):
            model(intent="tech_support")

    def test_numeric_bounds_are_enforced(self):
        model = build_response_model(
            rows(
                {
                    "name": "confidence",
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                }
            )
        )
        assert model(confidence=0.91).confidence == 0.91
        with pytest.raises(ValidationError):
            model(confidence=80)

    def test_optional_scalar_defaults_to_none(self):
        model = build_response_model(
            rows({"name": "phone", "type": "string", "required": False})
        )
        assert model().phone is None

    def test_optional_list_defaults_to_empty(self):
        """A None default on a list would break every downstream `contains`
        rule; an empty list is the honest "nothing was found"."""
        model = build_response_model(
            rows(
                {
                    "name": "missing_information",
                    "type": "list",
                    "item_type": "string",
                    "required": False,
                }
            )
        )
        assert model().missing_information == []


class TestNesting:
    def test_nested_objects_compile_and_validate(self):
        fields = rows(
            {
                "name": "process",
                "type": "object",
                "required": False,
                "fields": [
                    {
                        "name": "flow_rate",
                        "type": "object",
                        "required": False,
                        "fields": [
                            {"name": "value", "type": "number", "required": False},
                            {"name": "unit", "type": "string", "required": False},
                        ],
                    }
                ],
            }
        )
        model = build_response_model(fields)
        instance = model(process={"flow_rate": {"value": 15, "unit": "m3/h"}})
        assert instance.process.flow_rate.value == 15

    def test_list_of_objects_compiles(self):
        fields = rows(
            {
                "name": "line_items",
                "type": "list",
                "item_type": "object",
                "fields": [
                    {"name": "part", "type": "string"},
                    {"name": "quantity", "type": "integer"},
                ],
            }
        )
        model = build_response_model(fields)
        instance = model(line_items=[{"part": "seal-12", "quantity": 4}])
        assert instance.line_items[0].quantity == 4

    def test_excessive_nesting_is_rejected(self):
        deepest = {"name": "leaf", "type": "string"}
        spec: dict = deepest
        for index in range(8):
            spec = {
                "name": f"level_{index}",
                "type": "object",
                "fields": [spec],
            }
        with pytest.raises(ValueError, match="nests deeper"):
            validate_fields(parse_fields([spec]))

    def test_duplicate_names_at_one_level_are_rejected(self):
        with pytest.raises(ValueError, match="twice"):
            validate_fields(
                rows(
                    {"name": "intent", "type": "string"},
                    {"name": "intent", "type": "string"},
                )
            )


class TestAuthoringErrors:
    """Errors the row editor can produce, caught with a message that names the
    row rather than surfacing a Pydantic internal."""

    def test_enum_without_values_is_rejected(self):
        with pytest.raises(ValidationError, match="allowed value"):
            FieldSpec(name="intent", type="enum")

    def test_list_without_item_type_is_rejected(self):
        with pytest.raises(ValidationError, match="item_type"):
            FieldSpec(name="tags", type="list")

    def test_object_without_children_is_rejected(self):
        with pytest.raises(ValidationError, match="child field"):
            FieldSpec(name="customer", type="object")

    def test_scalar_with_children_is_rejected(self):
        with pytest.raises(ValidationError, match="cannot have child"):
            FieldSpec(
                name="language",
                type="string",
                fields=[FieldSpec(name="nested", type="string")],
            )

    def test_field_names_must_be_identifiers(self):
        with pytest.raises(ValidationError, match="letters, digits"):
            FieldSpec(name="customer name", type="string")

    def test_inverted_bounds_are_rejected(self):
        with pytest.raises(ValidationError, match="minimum greater"):
            FieldSpec(name="score", type="number", minimum=1, maximum=0)


class TestPathIndex:
    def test_paths_include_parents_and_children(self):
        paths = {
            item.path: item
            for item in field_paths(
                rows(
                    {
                        "name": "customer",
                        "type": "object",
                        "required": False,
                        "fields": [{"name": "company", "type": "string"}],
                    }
                )
            )
        }
        assert "customer" in paths
        assert "customer.company" in paths

    def test_child_of_optional_parent_may_be_unavailable(self):
        """A required field inside an optional object can still be absent. The
        mapping panel has to say so, or an author maps a value that is null half
        the time and never learns why."""
        paths = {
            item.path: item
            for item in field_paths(
                rows(
                    {
                        "name": "equipment",
                        "type": "object",
                        "required": False,
                        "fields": [{"name": "model", "type": "string"}],
                    }
                )
            )
        }
        assert paths["equipment.model"].required is True
        assert paths["equipment.model"].may_be_unavailable is True

    def test_list_of_objects_exposes_item_fields(self):
        paths = [
            item.path
            for item in field_paths(
                rows(
                    {
                        "name": "line_items",
                        "type": "list",
                        "item_type": "object",
                        "fields": [{"name": "part", "type": "string"}],
                    }
                )
            )
        ]
        assert "line_items" in paths
        assert "line_items.items.part" in paths

    def test_enum_values_reach_the_path_index(self):
        """This is what lets the rule editor offer a dropdown instead of a text
        box, and lets preflight reject a value outside the enum."""
        paths = field_paths(
            rows(
                {
                    "name": "urgency",
                    "type": "enum",
                    "enum_values": ["low", "critical"],
                }
            )
        )
        assert paths[0].enum_values == ["low", "critical"]


class TestJsonSchema:
    def test_json_schema_matches_the_compiled_model(self):
        fields = rows(
            {"name": "intent", "type": "enum", "enum_values": ["a", "b"]},
            {"name": "note", "type": "string", "required": False},
        )
        assert json_schema(fields) == build_response_model(fields).model_json_schema()

    def test_required_list_reflects_authoring(self):
        schema = json_schema(
            rows(
                {"name": "intent", "type": "string"},
                {"name": "note", "type": "string", "required": False},
            )
        )
        assert schema["required"] == ["intent"]


def test_contract_description_is_human_readable():
    """Sent to the model alongside native structured output: the schema
    constrains shape, this conveys intent."""
    text = describe_schema(
        rows(
            {
                "name": "intent",
                "type": "enum",
                "enum_values": ["complaint", "other"],
                "description": "What the customer wants.",
            }
        )
    )
    assert "intent" in text
    assert "one of: complaint, other" in text
    assert "What the customer wants." in text


def test_empty_rows_compile_to_no_schema():
    assert parse_fields(None) == []
    assert parse_fields([]) == []
