"""Tests for pure paper-to-Lean source rendering."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from autolean.generated_code import GeneratedCodeError
from autolean.paper import (
    Claim,
    _extract_arxiv_id,
    _parse_arxiv_html_theorems,
    _parse_page_selection,
    read_pdf,
    render_verification_source,
)


def test_verification_metadata_stays_inside_comments() -> None:
    claim = Claim(
        label='Theorem 1\n#eval IO.getEnv "TOKEN"',
        statement='A claim\nrun_tac do IO.getEnv "TOKEN"',
        proof_sketch='Sketch\n-/\n#eval IO.getEnv "TOKEN"',
        lean_code="theorem generated : True := by\n  sorry",
    )

    source = render_verification_source([claim], "Title -/\n#eval IO.getEnv")

    assert source.count("-/") == 1
    assert "\n#eval" not in source
    assert "\nrun_tac" not in source
    assert "theorem generated" in source


def test_verification_renderer_revalidates_claim_source() -> None:
    claim = Claim(
        label="Theorem 1",
        statement="A claim",
        lean_code="theorem generated : True := by run_tac do pure ()",
    )

    with pytest.raises(GeneratedCodeError):
        render_verification_source([claim])


def test_arxiv_identifier_preserves_explicit_version() -> None:
    assert _extract_arxiv_id("https://arxiv.org/pdf/2604.07408v3.pdf") == "2604.07408v3"
    assert _extract_arxiv_id("math/0411045v2") == "math/0411045v2"


def test_html_parser_handles_nested_blocks_and_math_alttext() -> None:
    html = """
    <html><body>
      <div class="ltx_theorem ltx_theorem_theorem">
        <span class="ltx_tag ltx_tag_theorem">Theorem 1.2.</span>
        <div class="ltx_para"><p>If <math alttext="x &lt; y"><mi>x</mi></math>,
        then the ordered pair exists.</p></div>
      </div>
      <div class="ltx_proof"><span class="ltx_tag">Proof.</span>
        <div><p>Apply the ordering axiom.</p></div> ∎
      </div>
      <div class="ltx_theorem ltx_theorem_lemma">
        <span class="ltx_tag">Lemma 1.3.</span><p>Every pair has two entries.</p>
      </div>
    </body></html>
    """

    claims = _parse_arxiv_html_theorems(html)

    assert [claim.label for claim in claims] == ["Theorem 1.2", "Lemma 1.3"]
    assert "x < y" in claims[0].statement
    assert claims[0].proof_sketch == "Apply the ordering axiom."
    assert claims[1].proof_sketch == ""


def test_page_selection_is_sorted_unique_and_bounds_checked() -> None:
    assert _parse_page_selection("3,1-2,2", 4) == [0, 1, 2]
    assert _parse_page_selection(None, 4) is None
    with pytest.raises(ValueError, match="outside"):
        _parse_page_selection("0", 4)
    with pytest.raises(ValueError, match="outside"):
        _parse_page_selection("3-2", 4)


def test_pdf_reader_uses_layout_ocr_and_preserves_page_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Document:
        def __enter__(self) -> Document:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def __len__(self) -> int:
            return 4

    seen: dict[str, object] = {}

    def to_markdown(path: str, **kwargs: object) -> list[dict[str, object]]:
        seen["path"] = path
        seen.update(kwargs)
        return [
            {"metadata": {"page_number": 1}, "text": "First page"},
            {"metadata": {"page_number": 3}, "text": "Third page"},
        ]

    monkeypatch.setitem(sys.modules, "pymupdf", SimpleNamespace(open=lambda path: Document()))
    monkeypatch.setitem(sys.modules, "pymupdf4llm", SimpleNamespace(to_markdown=to_markdown))
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF fixture")

    text = read_pdf(source, pages="3,1")

    assert seen["pages"] == [0, 2]
    assert seen["use_ocr"] is True
    assert seen["force_ocr"] is False
    assert text == "--- Page 1 ---\nFirst page\n\n--- Page 3 ---\nThird page"


def test_verification_source_records_extractor_input_identity() -> None:
    claim = Claim(
        label="Theorem 1",
        statement="True",
        lean_code="theorem generated : True := by\n  sorry",
        input_ref="https://arxiv.org/html/2604.07408v1",
        input_sha256="a" * 64,
    )

    source = render_verification_source([claim])

    assert "Extractor input: https://arxiv.org/html/2604.07408v1" in source
    assert f"Extractor input SHA-256: {'a' * 64}" in source
