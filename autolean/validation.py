"""Small validators shared by AutoLean's boundary records."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from datetime import datetime
from typing import TypeVar

_SHA256 = re.compile(r"[0-9a-f]{64}")
_T = TypeVar("_T")


def require_text(
    value: object,
    message: str,
    *,
    allow_empty: bool = False,
    error_type: type[Exception] = ValueError,
) -> None:
    """Require text, optionally admitting the empty string."""
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise error_type(message)


def require_texts(
    values: Iterable[object],
    message: str,
    *,
    allow_empty: bool = False,
    unique: bool = False,
    error_type: type[Exception] = ValueError,
) -> None:
    """Require a collection of text values with one shared policy."""
    materialized = tuple(values)
    if any(not isinstance(value, str) or (not allow_empty and not value.strip()) for value in materialized):
        raise error_type(message)
    if unique and len(set(materialized)) != len(materialized):
        raise error_type(message)


def require_optional_text(
    value: object,
    message: str,
    *,
    allow_empty: bool = False,
    error_type: type[Exception] = ValueError,
) -> None:
    """Require either no text or text with the requested empty policy."""
    if value is not None:
        require_text(
            value,
            message,
            allow_empty=allow_empty,
            error_type=error_type,
        )


def require_text_list(
    value: object,
    message: str,
    *,
    allow_empty: bool = False,
    unique: bool = False,
    error_type: type[Exception] = ValueError,
) -> None:
    """Require a list whose members share one text policy."""
    if not isinstance(value, list):
        raise error_type(message)
    require_texts(
        value,
        message,
        allow_empty=allow_empty,
        unique=unique,
        error_type=error_type,
    )


def require_bool(
    value: object,
    message: str,
    *,
    error_type: type[Exception] = ValueError,
) -> None:
    """Require a Boolean value."""
    if not isinstance(value, bool):
        raise error_type(message)


def require_optional_bool(
    value: object,
    message: str,
    *,
    error_type: type[Exception] = ValueError,
) -> None:
    """Require either no value or a Boolean value."""
    if value is not None:
        require_bool(value, message, error_type=error_type)


def require_int(
    value: object,
    message: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
    error_type: type[Exception] = ValueError,
) -> None:
    """Require an integer inside the requested closed bounds."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise error_type(message)
    if minimum is not None and value < minimum:
        raise error_type(message)
    if maximum is not None and value > maximum:
        raise error_type(message)


def require_optional_int(
    value: object,
    message: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
    error_type: type[Exception] = ValueError,
) -> None:
    """Require either no integer or one inside the requested bounds."""
    if value is not None:
        require_int(
            value,
            message,
            minimum=minimum,
            maximum=maximum,
            error_type=error_type,
        )


def require_ints(
    values: Iterable[object],
    message: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
    error_type: type[Exception] = ValueError,
) -> None:
    """Require integers that share one closed range."""
    for value in values:
        require_int(
            value,
            message,
            minimum=minimum,
            maximum=maximum,
            error_type=error_type,
        )


def require_number(
    value: object,
    message: str,
    *,
    minimum: float | None = None,
    minimum_inclusive: bool = True,
    maximum: float | None = None,
    error_type: type[Exception] = ValueError,
) -> None:
    """Require a finite real number inside the requested closed bounds."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise error_type(message)
    number = float(value)
    if not math.isfinite(number):
        raise error_type(message)
    if minimum is not None and (number < minimum or (not minimum_inclusive and number == minimum)):
        raise error_type(message)
    if maximum is not None and number > maximum:
        raise error_type(message)


def require_optional_number(
    value: object,
    message: str,
    *,
    minimum: float | None = None,
    minimum_inclusive: bool = True,
    maximum: float | None = None,
    error_type: type[Exception] = ValueError,
) -> None:
    """Require either no number or one inside the requested bounds."""
    if value is not None:
        require_number(
            value,
            message,
            minimum=minimum,
            minimum_inclusive=minimum_inclusive,
            maximum=maximum,
            error_type=error_type,
        )


def require_numbers(
    values: Iterable[object],
    message: str,
    *,
    minimum: float | None = None,
    minimum_inclusive: bool = True,
    maximum: float | None = None,
    error_type: type[Exception] = ValueError,
) -> None:
    """Require finite numbers that share one closed range."""
    for value in values:
        require_number(
            value,
            message,
            minimum=minimum,
            minimum_inclusive=minimum_inclusive,
            maximum=maximum,
            error_type=error_type,
        )


def require_sha256(
    value: object,
    message: str,
    *,
    allow_empty: bool = False,
    error_type: type[Exception] = ValueError,
) -> None:
    """Require a lowercase hexadecimal SHA-256 digest."""
    if allow_empty and value == "":
        return
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise error_type(message)


def require_sha256s(
    values: Iterable[object],
    message: str,
    *,
    allow_empty: bool = False,
    error_type: type[Exception] = ValueError,
) -> None:
    """Require digests that share one empty-value policy."""
    for value in values:
        require_sha256(
            value,
            message,
            allow_empty=allow_empty,
            error_type=error_type,
        )


def require_instance(
    value: object,
    expected: type[_T] | tuple[type[_T], ...],
    message: str,
    *,
    error_type: type[Exception] = ValueError,
) -> _T:
    """Return a value from one explicit runtime vocabulary."""
    if not isinstance(value, expected):
        raise error_type(message)
    return value


def require_optional_instance(
    value: object,
    expected: type[_T] | tuple[type[_T], ...],
    message: str,
    *,
    error_type: type[Exception] = ValueError,
) -> None:
    """Require either no value or one from a runtime vocabulary."""
    if value is not None:
        require_instance(value, expected, message, error_type=error_type)


def require_ordered_int_tuple(
    value: object,
    message: str,
    *,
    minimum: int,
    allow_empty: bool = True,
    error_type: type[Exception] = ValueError,
) -> None:
    """Require unique integers in strictly increasing tuple order."""
    if not isinstance(value, tuple) or (not allow_empty and not value):
        raise error_type(message)
    if any(isinstance(item, bool) or not isinstance(item, int) for item in value):
        raise error_type(message)
    if any(item < minimum for item in value):
        raise error_type(message)
    if tuple(sorted(set(value))) != value:
        raise error_type(message)


def require_aware_datetime(
    value: object,
    message: str,
    *,
    error_type: type[Exception] = ValueError,
) -> datetime:
    """Parse one ISO-8601 timestamp that carries a UTC offset."""
    require_text(value, message, error_type=error_type)
    assert isinstance(value, str)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise error_type(message) from error
    if parsed.tzinfo is None:
        raise error_type(message)
    return parsed
