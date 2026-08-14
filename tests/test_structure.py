"""Structural Lean context remains deterministic and advisory."""

from __future__ import annotations

from pathlib import Path

import pytest
import tree_sitter_language_pack

from autolean.structure import LeanStructureProvider, ParseQuality


@pytest.fixture
def provider() -> LeanStructureProvider:
    return LeanStructureProvider()


def test_target_context_includes_namespace_syntax_and_local_reference(
    provider: LeanStructureProvider,
    tmp_path: Path,
) -> None:
    path = tmp_path / "Target.lean"
    source = (
        "import Mathlib\n"
        "namespace Demo\n"
        "def helper (n : Nat) := n\n\n"
        "@[simp] theorem target (n : Nat) : helper n = n := by\n"
        "  sorry\n\n"
        "theorem later : True := by trivial\n"
        "end Demo\n"
    )

    context = provider.inspect(
        path,
        source,
        line=6,
        col=2,
        declaration_name="target",
    )

    assert context.quality is ParseQuality.COMPLETE
    assert context.namespace == "Demo"
    assert context.target is not None
    assert context.target.qualified_name == "Demo.target"
    assert context.target.signature.startswith("theorem target")
    assert context.syntax_path[-2:] == ("by", "sorry")
    assert [item.qualified_name for item in context.referenced_declarations] == ["Demo.helper"]
    assert [item.qualified_name for item in context.preceding_declarations] == ["Demo.helper"]
    assert [item.qualified_name for item in context.following_declarations] == ["Demo.later"]
    assert context.imports == ("Mathlib",)


def test_unrelated_parse_error_is_reported_as_recovered(
    provider: LeanStructureProvider,
    tmp_path: Path,
) -> None:
    source = "this is not Lean !!!\n\ntheorem target : True := by\n  sorry\n"

    context = provider.inspect(
        tmp_path / "Recovered.lean",
        source,
        line=4,
        col=2,
        declaration_name="target",
    )

    assert context.quality is ParseQuality.RECOVERED
    assert context.error_spans


def test_target_parse_error_is_explicit(
    provider: LeanStructureProvider,
    tmp_path: Path,
) -> None:
    source = "theorem target : True := by\n  sorry !!!\n"

    context = provider.inspect(
        tmp_path / "TargetRecovered.lean",
        source,
        line=2,
        col=2,
        declaration_name="target",
    )

    assert context.quality is ParseQuality.TARGET_RECOVERED


def test_context_is_stable_and_bounded(
    provider: LeanStructureProvider,
    tmp_path: Path,
) -> None:
    source = "theorem target : True := by\n  sorry\n"
    first = provider.inspect(tmp_path / "Stable.lean", source, line=2, col=2)
    second = provider.inspect(tmp_path / "Stable.lean", source, line=2, col=2)

    assert first == second
    assert first.sha256 == second.sha256
    assert len(first.render(max_chars=160)) <= 160
    assert first.render(max_chars=160).endswith("[structural context truncated]")


def test_source_change_invalidates_context_identity(
    provider: LeanStructureProvider,
    tmp_path: Path,
) -> None:
    path = tmp_path / "Changed.lean"
    first = provider.inspect(path, "theorem one : True := by\n  sorry\n", line=2, col=2)
    second = provider.inspect(path, "theorem two : True := by\n  sorry\n", line=2, col=2)

    assert first.source_sha256 != second.source_sha256
    assert first.target is not None
    assert second.target is not None
    assert (first.target.name, second.target.name) == ("one", "two")


def test_language_pack_failure_degrades_without_blocking_source_inspection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("AUTOLEAN_TREE_SITTER_LEAN_LIBRARY", raising=False)

    def fail_to_download(_: str) -> None:
        raise tree_sitter_language_pack.DownloadError("grammar service unavailable")

    monkeypatch.setattr(tree_sitter_language_pack, "get_parser", fail_to_download)
    provider = LeanStructureProvider()

    context = provider.inspect(
        tmp_path / "Unavailable.lean",
        "theorem target : True := by\n  sorry\n",
        line=2,
        col=2,
    )

    assert context.quality is ParseQuality.UNAVAILABLE
    assert context.unavailable_reason == "grammar service unavailable"


def test_language_pack_identity_hashes_the_cached_grammar(
    provider: LeanStructureProvider,
    tmp_path: Path,
) -> None:
    context = provider.inspect(
        tmp_path / "Identity.lean",
        "theorem target : True := by\n  sorry\n",
        line=2,
        col=2,
    )

    assert "grammar-sha256/" in context.parser
