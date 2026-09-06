"""Tests for the shared boundary-validation vocabulary."""

from __future__ import annotations

from datetime import datetime

import pytest

from autolean.validation import (
    require_aware_datetime,
    require_bool,
    require_int,
    require_number,
    require_ordered_int_tuple,
    require_sha256,
    require_text,
    require_texts,
)


@pytest.mark.parametrize("value", [True, 1.0, "1", None])
def test_integer_validation_rejects_non_integer_values(value: object) -> None:
    with pytest.raises(ValueError, match="integer"):
        require_int(value, "integer required")


@pytest.mark.parametrize("value", [True, float("inf"), float("nan"), "1"])
def test_number_validation_rejects_non_finite_or_non_numeric_values(value: object) -> None:
    with pytest.raises(ValueError, match="number"):
        require_number(value, "number required")


def test_text_and_digest_validation_share_exact_empty_value_semantics() -> None:
    require_text("", "text", allow_empty=True)
    require_texts(("", "value"), "texts", allow_empty=True, unique=True)
    require_sha256("", "digest", allow_empty=True)

    with pytest.raises(ValueError, match="text"):
        require_text("", "text required")
    with pytest.raises(ValueError, match="digest"):
        require_sha256("A" * 64, "digest required")


def test_boolean_validation_does_not_accept_integer_subclasses() -> None:
    require_bool(False, "boolean required")
    with pytest.raises(ValueError, match="boolean"):
        require_bool(0, "boolean required")


def test_ordered_integer_tuple_enforces_identity_and_order() -> None:
    require_ordered_int_tuple((1, 3), "pages", minimum=1, allow_empty=False)
    for value in ((1, 1), (2, 1), (0, 1), [1, 2]):
        with pytest.raises(ValueError, match="pages"):
            require_ordered_int_tuple(value, "pages", minimum=1, allow_empty=False)


def test_aware_datetime_requires_an_explicit_offset() -> None:
    parsed = require_aware_datetime("2026-08-21T12:00:00+02:00", "timestamp")
    assert isinstance(parsed, datetime)
    assert parsed.utcoffset() is not None

    with pytest.raises(ValueError, match="timestamp"):
        require_aware_datetime("2026-08-21T12:00:00", "timestamp")
