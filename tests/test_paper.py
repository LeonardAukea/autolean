"""Tests for pure paper-to-Lean source rendering."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from autolean.generated_code import GeneratedCodeError
from autolean.llm import Capabilities, DocumentInput, LLMResponse
from autolean.paper import (
    Claim,
    PaperDocument,
    PdfEngine,
    _extract_arxiv_id,
    _fetch_arxiv_html_with_lightpanda,
    _parse_arxiv_html_theorems,
    _parse_page_selection,
    extract_document_claims,
    materialize_paper,
    read_paper,
    read_pdf,
    render_verification_source,
)


def test_materialized_paper_preserves_exact_text_and_pdf(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF exact fixture")
    text = "First page.\n\n$e^{i\\pi} + 1 = 0$"
    document = PaperDocument(
        title="Exact fixture",
        text=text,
        input_ref="https://arxiv.org/abs/0000.00000",
        input_sha256="a" * 64,
        pdf_path=source,
        extractor="hybrid",
    )

    artifact = materialize_paper(document, tmp_path / "project")
    repeated = materialize_paper(document, tmp_path / "project")

    assert repeated == artifact
    assert artifact.text_sha256 == hashlib.sha256(text.encode()).hexdigest()
    assert artifact.pdf_path is not None
    assert artifact.pdf_path.read_bytes() == source.read_bytes()
    markdown = artifact.markdown_path.read_text(encoding="utf-8")
    assert markdown.endswith(text + "\n")
    assert "Input SHA-256: `" + "a" * 64 + "`" in markdown


def test_materialized_paper_rejects_non_digest_identity(tmp_path: Path) -> None:
    document = PaperDocument(title="Fixture", text="paper", input_sha256="../../escape")

    with pytest.raises(ValueError, match="paper input SHA-256"):
        materialize_paper(document, tmp_path / "project")


def test_document_claim_extraction_attaches_pdf_when_supported(tmp_path: Path) -> None:
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF fixture")
    seen: list[DocumentInput] = []

    class Backend:
        capabilities = Capabilities(document_inputs=True)

        def generate(self, *args: object, **kwargs: object) -> LLMResponse:
            raise AssertionError("text-only generation was used")

        def generate_with_documents(
            self,
            system: str,
            user: str,
            documents: tuple[DocumentInput, ...],
            **kwargs: object,
        ) -> LLMResponse:
            del system, user, kwargs
            seen.extend(documents)
            return LLMResponse(
                text="1. [Theorem 1]: Every proposition implies itself.",
                model="fixture",
            )

    document = PaperDocument(
        title="Fixture",
        text="Paper text.",
        pdf_path=source,
    )

    claims = extract_document_claims(document, Backend())  # type: ignore[arg-type]

    assert [claim.label for claim in claims] == ["Theorem 1"]
    assert len(seen) == 1
    assert seen[0].data == b"%PDF fixture"


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


def test_lightpanda_arxiv_fetch_blocks_private_networks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "lightpanda"
    executable.write_text("fixture", encoding="utf-8")
    executable.chmod(0o700)
    seen: dict[str, object] = {}

    def run(arguments: list[str], **kwargs: object) -> SimpleNamespace:
        seen.update(arguments=arguments, kwargs=kwargs)
        return SimpleNamespace(returncode=0, stdout="<html>" + "x" * 10001, stderr="")

    monkeypatch.setenv("AUTOLEAN_LIGHTPANDA", str(executable))
    monkeypatch.setattr("autolean.paper.subprocess.run", run)

    html = _fetch_arxiv_html_with_lightpanda(
        "https://arxiv.org/html/2606.07588v1",
        timeout=10,
    )

    arguments = seen["arguments"]
    assert isinstance(arguments, list)
    assert "--block-private-networks" in arguments
    assert "--disable-workers" in arguments
    assert "--disable-subframes" in arguments
    assert "--obey-robots" in arguments
    assert html.startswith("<html>")


def test_structured_arxiv_extraction_keeps_the_native_pdf(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF fixture")
    claim = Claim(
        label="Theorem 1",
        statement="Every proposition implies itself.",
        input_ref="https://arxiv.org/html/2606.07588v1",
        input_sha256="a" * 64,
    )
    monkeypatch.setattr("autolean.paper.extract_claims_from_html", lambda _: [claim])
    monkeypatch.setattr("autolean.paper.fetch_arxiv", lambda _: pdf)

    document = read_paper("https://arxiv.org/html/2606.07588v1")

    assert document.extractor == "arxiv-html"
    assert document.claims == [claim]
    assert document.pdf_path == pdf


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


def test_paddleocr_reader_sends_selected_pdf_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Document:
        selected: list[int] | None = None

        def __enter__(self) -> Document:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def __len__(self) -> int:
            return 4

        def select(self, pages: list[int]) -> None:
            self.selected = pages

        def tobytes(self, **kwargs: object) -> bytes:
            assert kwargs == {"garbage": 4, "deflate": True}
            return b"selected pdf"

    documents: list[Document] = []

    def open_document(path: str) -> Document:
        del path
        document = Document()
        documents.append(document)
        return document

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "errorCode": 0,
                "result": {
                    "layoutParsingResults": [
                        {"markdown": {"text": "Third page"}},
                        {"markdown": {"text": "First page"}},
                    ]
                },
            }

    seen: dict[str, object] = {}

    def post(url: str, **kwargs: object) -> Response:
        seen["url"] = url
        seen.update(kwargs)
        return Response()

    monkeypatch.setitem(sys.modules, "pymupdf", SimpleNamespace(open=open_document))
    monkeypatch.setitem(sys.modules, "pymupdf4llm", SimpleNamespace())
    monkeypatch.setattr("autolean.paper.httpx.post", post)
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF fixture")

    text = read_pdf(
        source,
        pages="3,1",
        engine=PdfEngine.PADDLEOCR_VL,
        paddleocr_url="http://127.0.0.1:8118",
    )

    assert documents[1].selected == [0, 2]
    payload = seen["json"]
    assert isinstance(payload, dict)
    assert payload["file"] == "c2VsZWN0ZWQgcGRm"
    assert seen["url"] == "http://127.0.0.1:8118/layout-parsing"
    assert text == "--- Page 1 ---\nThird page\n\n--- Page 3 ---\nFirst page"


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
