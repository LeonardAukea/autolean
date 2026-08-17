"""Tests for library builder — gap detection and library generation."""

from __future__ import annotations

from autolean.library import detect_missing_definitions, generate_library_source
from autolean.llm import LLMResponse


class TestDetectMissingDefinitions:
    def test_unknown_identifier(self) -> None:
        gaps = detect_missing_definitions("unknown identifier 'MyType'")
        assert len(gaps) == 1
        assert gaps[0].name == "MyType"

    def test_unknown_constant(self) -> None:
        gaps = detect_missing_definitions("unknown constant 'Foo.bar'")
        assert len(gaps) == 1
        assert gaps[0].name == "Foo.bar"

    def test_failed_to_synthesize(self) -> None:
        gaps = detect_missing_definitions("failed to synthesize instance Add MyType")
        assert len(gaps) == 1
        assert gaps[0].name == "Add"

    def test_no_gaps_in_normal_error(self) -> None:
        gaps = detect_missing_definitions("type mismatch, expected Nat got Bool")
        assert len(gaps) == 0

    def test_multiple_gaps(self) -> None:
        msg = "unknown identifier 'Foo'\nunknown constant 'Bar.baz'"
        gaps = detect_missing_definitions(msg)
        assert len(gaps) == 2
        names = {g.name for g in gaps}
        assert "Foo" in names
        assert "Bar.baz" in names

    def test_ignores_short_names(self) -> None:
        gaps = detect_missing_definitions("unknown identifier 'x'")
        assert len(gaps) == 0  # single char names are likely typos

    def test_stores_context(self) -> None:
        gaps = detect_missing_definitions(
            "unknown identifier 'MyDef'",
            context="theorem foo := sorry",
            file="Test.lean",
        )
        assert gaps[0].context == "theorem foo := sorry"
        assert gaps[0].file == "Test.lean"


def test_library_source_keeps_topic_inside_header_comment() -> None:
    def generate(system: str, prompt: str) -> LLMResponse:
        del system, prompt
        return LLMResponse(
            text="theorem generated : True := by\n  sorry",
            model="fixture",
        )

    source = generate_library_source('top -/\n#eval IO.getEnv "TOKEN"', generate)

    assert source.count("-/") == 1
    assert "\n#eval" not in source
    assert "theorem generated" in source
