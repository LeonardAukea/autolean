"""Tests for library builder — gap detection and library generation."""

from __future__ import annotations

import pytest

from autolean.generated_code import GeneratedCodeError
from autolean.library import (
    FILL_GAP_SYSTEM,
    MissingDefinition,
    detect_missing_definitions,
    fill_gap,
    generate_library_source,
)
from autolean.llm import LLMResponse
from autolean.provenance import sha256_text


class TestDetectMissingDefinitions:
    def test_unknown_identifier(self) -> None:
        gaps = detect_missing_definitions("unknown identifier 'MyType'")
        assert len(gaps) == 1
        assert gaps[0].name == "MyType"

    def test_unknown_constant(self) -> None:
        gaps = detect_missing_definitions("unknown constant 'Foo.bar'")
        assert len(gaps) == 1
        assert gaps[0].name == "Foo.bar"

    def test_instance_synthesis_is_not_a_missing_declaration(self) -> None:
        gaps = detect_missing_definitions("failed to synthesize instance Add MyType")
        assert gaps == []

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


def test_fill_gap_returns_the_generating_response_and_prompt_identity() -> None:
    request: dict[str, str] = {}

    def generate(system: str, prompt: str) -> LLMResponse:
        request.update(system=system, prompt=prompt)
        return LLMResponse(
            text="def Missing : True := True.intro",
            model="gap-model",
            input_tokens=77,
            output_tokens=9,
            duration_seconds=3.0,
        )

    generated = fill_gap(
        MissingDefinition(
            name="Missing",
            error_message="unknown identifier 'Missing'",
            context="theorem target : True := by exact Missing",
            file="AutoLean/Target.lean",
        ),
        generate,
    )

    assert generated is not None
    assert generated.code == "def Missing : True := True.intro"
    assert generated.response.model == "gap-model"
    assert generated.response.input_tokens == 77
    assert generated.response.output_tokens == 9
    assert generated.prompt_sha256 == sha256_text(f"{request['system']}\0{request['prompt']}")
    assert request["system"] == FILL_GAP_SYSTEM


def test_fill_gap_rejects_namespace_injection() -> None:
    def generate(system: str, prompt: str) -> LLMResponse:
        del system, prompt
        return LLMResponse(
            text="namespace Hijack\ndef Missing : Nat := 0\nend Hijack",
            model="fixture",
        )

    with pytest.raises(GeneratedCodeError):
        fill_gap(
            MissingDefinition(
                name="Missing",
                error_message="unknown identifier 'Missing'",
                context="",
                file="AutoLean/Target.lean",
            ),
            generate,
        )
