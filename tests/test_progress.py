"""Progress observations preserve explicit outcomes and bounded payloads."""

from __future__ import annotations

import asyncio
import json

import pytest

from autolean.progress import MAX_DETAIL, MAX_EVENT_BYTES, PROGRESS_PREFIX, ProgressEvent, ProgressKind
from autolean.workbench import _terminal_lines


def test_progress_round_trip_preserves_lean_and_literal_markup() -> None:
    event = ProgressEvent(
        ProgressKind.CANDIDATE, "Awaiting Lean", "intro x\nexact h x -- [red] ∀ x", "Gromov.question", 2, 1
    )
    assert ProgressEvent.from_line(PROGRESS_PREFIX + event.to_json()) == event
    assert ProgressEvent.from_line("ordinary compiler output") is None


def test_progress_displays_the_same_instant_with_an_explicit_timezone() -> None:
    event = ProgressEvent(ProgressKind.PHASE, "Working", timestamp="2026-09-06T19:00:00+02:00")
    assert event.time_label == "17:00:00Z"


@pytest.mark.parametrize(
    "change",
    [
        {"schema": "unknown"},
        {"kind": "proved_without_lean"},
        {"cycle": True},
        {"attempt": -1},
        {"message": ""},
        {"detail": "x" * (MAX_DETAIL + 1)},
        {"timestamp": "2026-09-06T12:00:00"},
        {"extra": "unrecognized"},
    ],
)
def test_progress_rejects_invalid_records(change: dict[str, object]) -> None:
    data = json.loads(ProgressEvent(ProgressKind.PHASE, "Working").to_json())
    data.update(change)
    with pytest.raises(ValueError):
        ProgressEvent.from_line(PROGRESS_PREFIX + json.dumps(data))


def test_progress_rejects_excessive_json_nesting_within_the_byte_bound() -> None:
    record = PROGRESS_PREFIX + "[" * 30000 + "0" + "]" * 30000
    assert len(record.encode()) < MAX_EVENT_BYTES
    with pytest.raises(ValueError, match="invalid progress event"):
        ProgressEvent.from_line(record)


def test_terminal_stream_preserves_split_events_and_bounds_long_lines() -> None:
    async def exercise() -> None:
        reader = asyncio.StreamReader()
        event = ProgressEvent(ProgressKind.FEEDBACK, "FAIL_BUILD", "⊢ False")
        line = PROGRESS_PREFIX + event.to_json()
        payload = (line + "\n" + "x" * (MAX_EVENT_BYTES * 3 + 7) + "\nlast").encode()
        for start in range(0, len(payload), 3):
            reader.feed_data(payload[start : start + 3])
        reader.feed_eof()
        lines = [line async for line in _terminal_lines(reader)]
        assert ProgressEvent.from_line(lines[0]) == event
        assert "".join(lines[1:-1]) == "x" * (MAX_EVENT_BYTES * 3 + 7)
        assert max(len(line.encode()) for line in lines) <= MAX_EVENT_BYTES
        assert lines[-1] == "last"

    asyncio.run(exercise())
