"""Reusable assertions for testing Pydantic model validation.

Complements ad hoc try/except blocks scattered across test files with a
consistent way to assert a model accepts or rejects a specific field value,
keyed to pydantic's own error-type catalog:
https://docs.pydantic.dev/latest/errors/validation_errors/

Use this whenever a bug traces back to "a config field got the wrong shape"
(the exact class of bug behind HorizonHTMLProposalRendererConfig.content
receiving `{}` instead of a string) — assert_field_rejects_non_string is the
one-liner for that specific, recurring case.
"""
from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ValidationError


def assert_accepts(model_cls: type[BaseModel], **kwargs: Any) -> BaseModel:
    """Construct model_cls(**kwargs) and assert it succeeds.

    Returns the instance so callers can assert on its fields too.
    """
    return model_cls(**kwargs)


def assert_rejects(
    model_cls: type[BaseModel],
    *,
    error_type: str | None = None,
    field: str | None = None,
    **kwargs: Any,
) -> ValidationError:
    """Construct model_cls(**kwargs) and assert it raises ValidationError.

    error_type: pydantic's error ``type`` string (e.g. "string_type",
    "missing", "int_parsing") — see the errors catalog linked above.
    field: dotted ``loc`` path (e.g. "content") the error must attach to.

    Returns the ValidationError for further inspection by the caller.
    """
    with pytest.raises(ValidationError) as caught:
        model_cls(**kwargs)
    exc = caught.value
    if error_type is not None or field is not None:
        matches = [
            err
            for err in exc.errors()
            if (error_type is None or err["type"] == error_type)
            and (field is None or ".".join(str(p) for p in err["loc"]) == field)
        ]
        assert matches, (
            f"expected an error with type={error_type!r} field={field!r} "
            f"on {model_cls.__name__}, got: {exc.errors()}"
        )
    return exc


def assert_field_rejects_non_string(
    model_cls: type[BaseModel],
    field: str,
    *,
    valid_kwargs: dict[str, Any],
    bad_values: tuple[Any, ...] = ({}, [], None, 0),
) -> None:
    """Assert `field` is a strict string field on `model_cls`.

    First asserts `valid_kwargs` constructs successfully (so a failure below
    can only be attributed to `field`, not some other required field), then
    asserts each of `bad_values` raises pydantic's "string_type" error
    attached to exactly `field`.

    This is the direct regression guard for the bug class behind
    HorizonHTMLProposalRendererConfig(content={}) — a template reference
    that resolved to the wrong shape (e.g. TransformAgent.parsed with no
    output_schema, which is always {}) should fail loudly and specifically
    at this field, not silently coerce or fail somewhere unrelated.
    """
    assert_accepts(model_cls, **valid_kwargs)
    for bad in bad_values:
        assert_rejects(
            model_cls,
            error_type="string_type",
            field=field,
            **{**valid_kwargs, field: bad},
        )
