"""Tests for pure paper-to-Lean source rendering."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from autolean.generated_code import GeneratedCodeError
from autolean.llm import Capabilities, DocumentInput, LLMResponse
from autolean.paper import (
    Claim,
    ClaimDisposition,
    PaperDocument,
    PdfEngine,
    _extract_arxiv_id,
    _fetch_arxiv_html_with_lightpanda,
    _parse_arxiv_html_theorems,
    _parse_page_selection,
    claim_disposition,
    extract_claims_from_markdown,
    extract_document_claims,
    materialize_paper,
    read_paper,
    read_pdf,
)
from autolean.paper_evidence import (
    analyze_paper_structure,
    bind_reviewed_paper,
    mark_reviewed_paper_elaborated,
    render_verification_source,
    write_paper_coverage,
    write_paper_plan,
)
from autolean.paper_profiles import IONESCU_TULCEA_V5, PaperProfileError
from autolean.strategy import PlanAttempt, parse_proof_plan


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
    assert "Source SHA-256: `" + "a" * 64 + "`" in markdown
    assert artifact.pdf_sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    assert f"PDF SHA-256: `{artifact.pdf_sha256}`" in markdown


def test_paper_plan_preserves_the_exact_model_response(tmp_path: Path) -> None:
    artifact = materialize_paper(
        PaperDocument(title="Fixture", text="A theorem.", input_sha256="a" * 64),
        tmp_path,
    )
    payload: dict[str, object] = {
        "objective": "Audit one exact declaration.",
        "formalization": [],
        "observations": [],
        "invariants": [],
        "obstructions": [],
        "reductions": [],
        "premises": [],
        "methods": ["Elaborate the generated declaration."],
        "partial_results": [],
        "risks": ["Elaboration does not prove source fidelity."],
        "completion_criteria": ["Lean reports zero errors."],
        "checkpoints": [],
        "revision_triggers": [],
    }
    raw_response = json.dumps(payload, indent=2)
    plan = parse_proof_plan(raw_response)
    response = PlanAttempt(
        attempt=1,
        guidance=("Keep the evidence boundary explicit.",),
        response=raw_response,
        model="opus",
        input_tokens=101,
        output_tokens=202,
        duration_seconds=3.5,
    )

    path = write_paper_plan(
        artifact,
        plan,
        model="opus",
        backend="claude_cli",
        responses=(response,),
    )
    record = json.loads(path.read_text(encoding="utf-8"))

    assert record["schema"] == "autolean.paper-plan.v2"
    assert record["responses"][0]["response"] == raw_response
    assert record["responses"][0]["response_sha256"] == response.response_sha256
    assert record["accepted_response_sha256"] == response.response_sha256
    assert len(record["trace_sha256"]) == 64


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


def test_html_parser_does_not_promote_proof_environments_to_claims() -> None:
    html = """
    <html><body>
      <div class="ltx_theorem ltx_theorem_thm">
        <span class="ltx_tag">Theorem 1.</span><p>A statement.</p>
      </div>
      <div class="ltx_theorem ltx_theorem_proof ltx_proof">
        <span class="ltx_tag">Proof.</span><p>The proof.</p>
      </div>
    </body></html>
    """

    claims = _parse_arxiv_html_theorems(html)

    assert [(claim.label, claim.kind) for claim in claims] == [("Theorem 1", "theorem")]
    assert claims[0].proof_sketch == "The proof."


def test_layout_markdown_recovers_numbered_paper_inventory() -> None:
    markdown = """
    # A probability paper

    **Definition 2.1.** A kernel sends points to probability measures.

    **Theorem 2.2** (Ionescu-Tulcea) **.** There is a unique trajectory
    kernel with the prescribed finite-dimensional marginals.

    The construction uses an extension theorem.

    **Lemma 3.1.** Partial trajectories compose.

    theorem partialTraj_comp_partialTraj : True := by trivial

    **Conjecture 4.1.** Brownian motion admits a future extension.
    """

    claims = extract_claims_from_markdown(markdown)

    assert [claim.label for claim in claims] == [
        "Definition 2.1",
        "Theorem 2.2",
        "Lemma 3.1",
        "Conjecture 4.1",
    ]
    assert claims[0].disposition is ClaimDisposition.DEFINE
    assert claims[1].disposition is ClaimDisposition.PROVE
    assert claims[2].proof_sketch.startswith("theorem partialTraj_comp_partialTraj")
    assert claims[3].disposition is ClaimDisposition.OPEN


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("thm", ClaimDisposition.PROVE),
        ("definition", ClaimDisposition.DEFINE),
        ("conjecture", ClaimDisposition.OPEN),
        ("remark", ClaimDisposition.CONTEXT),
        ("proof", ClaimDisposition.CONTEXT),
        ("unknown environment", ClaimDisposition.CONTEXT),
    ],
)
def test_claim_disposition_is_fail_closed(
    kind: str,
    expected: ClaimDisposition,
) -> None:
    assert claim_disposition(kind) is expected


def test_paper_coverage_records_every_item_without_proving_conjectures(
    tmp_path: Path,
) -> None:
    claims = [
        Claim("Theorem 1", "A proved result.", kind="theorem", lean_code="theorem t : True := by trivial"),
        Claim("Definition 1", "A definition.", kind="definition"),
        Claim("Conjecture 1", "An open problem.", kind="conjecture"),
        Claim("Remark 1", "Context.", kind="remark"),
    ]
    document = PaperDocument(title="Fixture", claims=claims, input_sha256="a" * 64)
    artifact = materialize_paper(document, tmp_path / "project")

    coverage = write_paper_coverage(artifact, claims)
    analysis = analyze_paper_structure(claims)
    source = render_verification_source(claims)

    assert analysis["by_disposition"] == {
        "prove": 1,
        "define": 1,
        "context": 1,
        "open": 1,
    }
    assert coverage.is_file()
    assert '"disposition": "open"' in coverage.read_text(encoding="utf-8")
    assert "theorem t" in source
    assert "Conjecture 1" in source
    assert "Recorded as a source boundary" in source


def _ionescu_tulcea_claims() -> list[Claim]:
    return [
        Claim(
            item.label,
            f"Extracted statement for {item.label}.",
            kind=item.label.split()[0],
            input_ref="https://arxiv.org/pdf/2506.18616v5.pdf",
            input_sha256=IONESCU_TULCEA_V5.pdf_sha256,
        )
        for item in IONESCU_TULCEA_V5.items
    ]


def test_reviewed_paper_binds_all_items_to_closed_lean_aliases(tmp_path: Path) -> None:
    claims = _ionescu_tulcea_claims()
    artifact = materialize_paper(
        PaperDocument(
            title=IONESCU_TULCEA_V5.title,
            claims=claims,
            input_sha256=IONESCU_TULCEA_V5.pdf_sha256,
        ),
        tmp_path,
    )
    artifact = artifact.__class__(
        artifact.markdown_path,
        artifact.pdf_path,
        artifact.input_sha256,
        artifact.text_sha256,
        IONESCU_TULCEA_V5.pdf_sha256,
    )

    profile = bind_reviewed_paper(claims, artifact)

    assert profile is IONESCU_TULCEA_V5
    assert len(claims) == 25
    assert sum(len(claim.evidence_names) for claim in claims) == 33
    assert all("noncomputable abbrev" in claim.lean_code for claim in claims)
    assert all("sorry" not in claim.lean_code for claim in claims)
    assert claims[10].lean_declarations == (
        "ProbabilityTheory.Kernel.traj",
        "ProbabilityTheory.Kernel.traj_map_frestrictLe",
        "ProbabilityTheory.Kernel.eq_traj",
    )


def test_reviewed_paper_coverage_records_elaborated_item_mappings(tmp_path: Path) -> None:
    claims = _ionescu_tulcea_claims()
    artifact = materialize_paper(
        PaperDocument(
            title=IONESCU_TULCEA_V5.title,
            claims=claims,
            input_sha256=IONESCU_TULCEA_V5.pdf_sha256,
        ),
        tmp_path,
    )
    artifact = artifact.__class__(
        artifact.markdown_path,
        artifact.pdf_path,
        artifact.input_sha256,
        artifact.text_sha256,
        IONESCU_TULCEA_V5.pdf_sha256,
    )
    profile = bind_reviewed_paper(claims, artifact)
    assert profile is not None
    mark_reviewed_paper_elaborated(claims, profile)

    coverage = write_paper_coverage(
        artifact,
        claims,
        lean_evidence={
            "declaration_count": 33,
            "error_count": 0,
            "module": "AutoLean/Evidence.lean",
            "source_sha256": "b" * 64,
            "success": True,
        },
    )
    record = json.loads(coverage.read_text(encoding="utf-8"))

    assert record["schema"] == "autolean.paper-coverage.v2"
    assert record["profile"]["id"] == IONESCU_TULCEA_V5.id
    assert record["total_items"] == 25
    assert record["elaborated_items"] == 25
    assert record["lean_evidence"]["declaration_count"] == 33
    assert record["lean_evidence"]["success"] is True
    assert {item["status"] for item in record["claims"]} == {"elaborated"}
    assert all(item["statement"] for item in record["claims"])
    assert all(len(item["statement_sha256"]) == 64 for item in record["claims"])


def test_elaborated_paper_coverage_requires_lean_evidence(tmp_path: Path) -> None:
    claims = _ionescu_tulcea_claims()
    artifact = materialize_paper(
        PaperDocument(
            title=IONESCU_TULCEA_V5.title,
            claims=claims,
            input_sha256=IONESCU_TULCEA_V5.pdf_sha256,
        ),
        tmp_path,
    )
    artifact = artifact.__class__(
        artifact.markdown_path,
        artifact.pdf_path,
        artifact.input_sha256,
        artifact.text_sha256,
        IONESCU_TULCEA_V5.pdf_sha256,
    )
    profile = bind_reviewed_paper(claims, artifact)
    assert profile is not None
    mark_reviewed_paper_elaborated(claims, profile)

    with pytest.raises(ValueError, match="requires Lean evidence"):
        write_paper_coverage(artifact, claims)


def test_reviewed_paper_rejects_an_incomplete_inventory(tmp_path: Path) -> None:
    claims = _ionescu_tulcea_claims()[:-1]
    artifact = materialize_paper(
        PaperDocument(title="Fixture", claims=claims, input_sha256="a" * 64),
        tmp_path,
    )
    artifact = artifact.__class__(
        artifact.markdown_path,
        artifact.pdf_path,
        artifact.input_sha256,
        artifact.text_sha256,
        IONESCU_TULCEA_V5.pdf_sha256,
    )

    with pytest.raises(PaperProfileError, match=r"missing: Theorem 4\.1"):
        bind_reviewed_paper(claims, artifact)


def test_verification_source_starts_with_lean_imports() -> None:
    source = render_verification_source(
        [Claim("Theorem 1", "A statement.", lean_code="theorem t : True := by trivial")]
    )

    assert source.startswith("import Mathlib\n\n/-!")


def test_reviewed_profile_uses_its_exact_import_closure() -> None:
    source = render_verification_source(
        [],
        IONESCU_TULCEA_V5.title,
        imports=IONESCU_TULCEA_V5.imports,
    )

    assert source.startswith("import Mathlib.Probability.ProductMeasure\n")
    assert "\nimport Mathlib\n" not in source


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


def test_html_claims_are_read_from_the_revision_the_pdf_pins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`fetch_arxiv` hashes the latest revision, so claims come from it."""
    from autolean.paper import extract_claims_from_html

    requested: list[str] = []

    class Response:
        status_code = 404
        text = ""
        content = b""
        url = ""

    def get(url: str, **kwargs: object) -> Response:
        del kwargs
        requested.append(url)
        return Response()

    monkeypatch.setattr("autolean.paper.httpx.get", get)
    monkeypatch.setattr("autolean.paper._fetch_arxiv_html_with_lightpanda", lambda *a, **k: "")

    assert extract_claims_from_html("2501.12345") == []
    assert requested[0] == "https://arxiv.org/html/2501.12345", (
        f"claims were read from a revision the PDF does not pin: {requested}"
    )
    assert "https://arxiv.org/html/2501.12345v1" in requested, "no fallback for an unrendered revision"
