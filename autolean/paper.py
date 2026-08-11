"""Paper verification — extract claims from PDFs and formalize in Lean 4.

Workflow:
  1. Read a math paper (arXiv HTML, PDF, or abstract)
  2. Extract theorem/lemma/definition environments (structured HTML or LLM)
  3. LLM formalizes each claim as a Lean 4 theorem with sorry
  4. Writes a .lean file into the workspace
  5. The normal agent loop attempts proofs

Text extraction strategies (tried in order):
  1. arXiv native HTML — structured theorem environments, proofs, math
  2. PyMuPDF4LLM — layout-aware PDF Markdown and selective OCR
  3. arXiv API abstract — minimal but always works
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from rich.console import Console

from autolean.generated_code import (
    GeneratedCodeError,
    safe_lean_comment_text,
    validate_generated_declarations,
)
from autolean.llm import GenerateFn, LLMError

console = Console()


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class Claim:
    """A mathematical claim extracted from a paper."""

    label: str  # "Theorem 3.1", "Lemma 2", "Proposition 4.5"
    statement: str  # Natural language statement
    lean_name: str = ""  # Generated Lean identifier
    lean_code: str = ""  # Formalized Lean 4 code
    proof_sketch: str = ""  # Proof from the paper (if available)
    kind: str = ""  # "theorem", "lemma", "definition", "proposition", etc.
    input_ref: str = ""  # source delivered to the extractor
    input_sha256: str = ""  # exact extractor input bytes


# ---------------------------------------------------------------------------
# arXiv helpers
# ---------------------------------------------------------------------------


def _extract_arxiv_id(source: str) -> str | None:
    """Extract arXiv ID from a URL or raw ID string."""
    source = source.strip().split("?", 1)[0].split("#", 1)[0].rstrip("/")
    source = source.removesuffix(".pdf")
    for prefix in [
        "https://arxiv.org/abs/",
        "https://arxiv.org/pdf/",
        "http://arxiv.org/abs/",
        "http://arxiv.org/pdf/",
        "https://arxiv.org/html/",
        "https://ar5iv.labs.arxiv.org/html/",
    ]:
        if source.startswith(prefix):
            identifier = source[len(prefix) :]
            return identifier if _is_arxiv_id(identifier) else None

    # Bare ID like "2604.07408" or "math/0411045"
    return source if _is_arxiv_id(source) else None


def _is_arxiv_id(value: str) -> bool:
    return bool(
        re.fullmatch(r"\d{4}\.\d{4,5}(?:v\d+)?", value)
        or re.fullmatch(r"[a-z-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?", value)
    )


def fetch_arxiv(arxiv_id_or_url: str, output_dir: Path | None = None) -> Path:
    """Download a paper PDF from arXiv."""
    arxiv_id = _extract_arxiv_id(arxiv_id_or_url) or arxiv_id_or_url
    pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
    output_dir = output_dir or Path("/tmp")
    output_path = output_dir / f"arxiv_{arxiv_id.replace('/', '_')}.pdf"

    if output_path.exists():
        console.print(f"  [dim]Using cached: {output_path}[/]")
        return output_path

    console.print(f"  Downloading [cyan]{pdf_url}[/]...")
    with httpx.stream("GET", pdf_url, follow_redirects=True, timeout=120.0) as resp:
        resp.raise_for_status()
        with open(output_path, "wb") as f:
            for chunk in resp.iter_bytes():
                f.write(chunk)

    size_mb = output_path.stat().st_size / 1024 / 1024
    console.print(f"  [green]Downloaded[/] ({size_mb:.1f} MB)")
    return output_path


# ---------------------------------------------------------------------------
# Structured HTML extraction (best quality — no LLM needed)
# ---------------------------------------------------------------------------


def extract_claims_from_html(arxiv_id: str, *, timeout: float = 60.0) -> list[Claim]:
    """Extract theorem/lemma/definition environments directly from arXiv HTML.

    arXiv's native HTML rendering (`arxiv.org/html/ID`) uses structured
    classes like ltx_theorem_thm, ltx_theorem_lem, ltx_tag, ltx_proof.
    We parse these directly — no LLM needed for finding claims.

    Returns list of Claims with label, statement, kind, and proof_sketch.
    Returns empty list if HTML is unavailable or has no theorem environments.
    """
    versioned = re.search(r"v\d+$", arxiv_id) is not None
    identifiers = [arxiv_id] if versioned else [f"{arxiv_id}v1", arxiv_id]
    for identifier in identifiers:
        url = f"https://arxiv.org/html/{identifier}"
        console.print(f"  Fetching: [cyan]{url}[/]...")
        try:
            resp = httpx.get(url, follow_redirects=True, timeout=timeout)
            if resp.status_code == 200 and len(resp.text) > 10000:
                claims = _parse_arxiv_html_theorems(resp.text)
                if claims:
                    digest = hashlib.sha256(resp.content).hexdigest()
                    for claim in claims:
                        claim.input_ref = str(resp.url)
                        claim.input_sha256 = digest
                    console.print(f"  [green]Extracted {len(claims)} theorem environments from HTML[/]")
                    return claims
        except httpx.HTTPError as e:
            console.print(f"  [dim]{url}: {e}[/]")

    return []


def _parse_arxiv_html_theorems(html: str) -> list[Claim]:
    """Parse theorem environments from arXiv native HTML.

    The HTML uses:
      <div class="ltx_theorem ltx_theorem_theorem"> ... </div>
      <div class="ltx_proof"> ... </div>
    with <span class="ltx_tag ltx_tag_theorem">Theorem 1.2</span> inside.

    Proof blocks are separate divs that follow theorem blocks.
    We match them by proximity in the HTML.
    """
    from bs4 import BeautifulSoup, Tag

    soup = BeautifulSoup(html, "html.parser")
    ordered: list[tuple[str, Tag, str]] = []
    for node in soup.find_all("div"):
        raw_classes = node.get("class")
        if isinstance(raw_classes, str):
            classes = {raw_classes}
        elif raw_classes is None:
            classes = set()
        else:
            classes = {str(value) for value in raw_classes}
        theorem_class = next(
            (name for name in classes if name.startswith("ltx_theorem_")),
            None,
        )
        if "ltx_theorem" in classes and theorem_class is not None:
            ordered.append(("theorem", node, theorem_class.removeprefix("ltx_theorem_")))
        elif "ltx_proof" in classes:
            ordered.append(("proof", node, ""))

    claims: list[Claim] = []
    for index, (node_type, node, kind) in enumerate(ordered):
        if node_type != "theorem":
            continue

        proof_sketch = ""
        for next_type, next_node, _ in ordered[index + 1 :]:
            if next_type == "theorem":
                break
            proof_sketch = _html_fragment_text(next_node, remove_tags=True)
            proof_sketch = re.sub(r"^Proof\.?\s*", "", proof_sketch, flags=re.IGNORECASE)
            proof_sketch = proof_sketch.rstrip("∎").strip()
            break

        fragment = BeautifulSoup(str(node), "html.parser")
        theorem = fragment.find("div")
        if theorem is None:
            continue
        label_node = theorem.select_one(".ltx_tag")
        label = (
            _html_fragment_text(label_node).rstrip(".") if isinstance(label_node, Tag) else kind.capitalize()
        )
        if label_node is not None:
            label_node.decompose()
        for proof in theorem.select(".ltx_proof"):
            proof.decompose()
        statement = _html_fragment_text(theorem)

        if statement and len(statement) > 10:
            claims.append(
                Claim(
                    label=label,
                    statement=statement[:1000],
                    lean_name=_to_lean_name(label),
                    kind=kind,
                    proof_sketch=proof_sketch[:500],
                )
            )

    return claims


def _strip_html(text: str) -> str:
    """Remove HTML tags and normalize whitespace, preserving math notation."""
    from bs4 import BeautifulSoup

    return _html_fragment_text(BeautifulSoup(text, "html.parser"))


def _html_fragment_text(fragment: Any, *, remove_tags: bool = False) -> str:
    """Extract normalized text while preserving LaTeX carried by MathML."""
    from bs4 import BeautifulSoup, Tag

    root = BeautifulSoup(str(fragment), "html.parser")
    for math in root.find_all("math"):
        assert isinstance(math, Tag)
        alt = math.get("alttext") or math.get("alt") or math.get_text(" ", strip=True)
        math.replace_with(f" {alt} ")
    if remove_tags:
        for tag in root.select(".ltx_tag"):
            tag.decompose()
    return " ".join(root.stripped_strings)


# ---------------------------------------------------------------------------
# PDF extraction
# ---------------------------------------------------------------------------


def read_pdf(path: Path, pages: str | None = None) -> str:
    """Extract layout-aware Markdown with selective OCR."""
    try:
        import pymupdf
        import pymupdf4llm
    except ImportError as e:
        raise ImportError(
            "PyMuPDF4LLM is required for PDF reading.\n"
            "Install the layout and OCR stack with: uv sync --extra pdf"
        ) from e

    with pymupdf.open(str(path)) as document:
        page_indices = _parse_page_selection(pages, len(document))

    chunks = pymupdf4llm.to_markdown(
        str(path),
        pages=page_indices,
        page_chunks=True,
        show_progress=False,
        use_ocr=True,
        force_ocr=False,
        write_images=False,
        embed_images=False,
    )
    if not isinstance(chunks, list):
        raise RuntimeError("PyMuPDF4LLM returned an unexpected document shape")

    text_parts: list[str] = []
    for chunk in chunks:
        if not isinstance(chunk, dict):
            raise RuntimeError("PyMuPDF4LLM returned a malformed page chunk")
        metadata = chunk.get("metadata")
        text = chunk.get("text")
        if not isinstance(metadata, dict) or not isinstance(text, str):
            raise RuntimeError("PyMuPDF4LLM page metadata is incomplete")
        page_number = metadata.get("page_number")
        if not isinstance(page_number, int):
            raise RuntimeError("PyMuPDF4LLM omitted the page number")
        if text.strip():
            text_parts.append(f"--- Page {page_number} ---\n{text.strip()}")

    result = "\n\n".join(text_parts)

    if not result.strip():
        console.print(
            "  [yellow]PyMuPDF4LLM extracted no text.[/]\n"
            "  Check the OCR runtime or select relevant pages with --pages."
        )

    return result


def _parse_page_selection(pages: str | None, page_count: int) -> list[int] | None:
    """Parse one-indexed page ranges into canonical zero-indexed order."""
    if pages is None:
        return None
    selected: set[int] = set()
    for raw_part in pages.split(","):
        part = raw_part.strip()
        if not part:
            raise ValueError("page selection contains an empty item")
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if match is None:
            raise ValueError(f"invalid page selection: {part!r}")
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if start < 1 or end < start or end > page_count:
            raise ValueError(f"page range {part!r} is outside this {page_count}-page document")
        selected.update(range(start - 1, end))
    return sorted(selected)


# ---------------------------------------------------------------------------
# LLM-based claim extraction (fallback for non-structured sources)
# ---------------------------------------------------------------------------

EXTRACT_CLAIMS_PROMPT = """\
You are analyzing a mathematics paper. List ALL theorems, lemmas, propositions, \
corollaries, conjectures, and key definitions.

For each, write exactly this format:
N. [Type X.Y]: precise mathematical statement

Example:
1. [Theorem 2.1]: For every connected graph G with n vertices, the chromatic \
polynomial P(G, k) satisfies P(G, k) > 0 for all k >= n.
2. [Lemma 3.4]: If H is a subgraph of G, then chi(H) <= chi(G).

Be precise — include all hypotheses, conditions, and conclusions. \
Use LaTeX notation for math ($...$).

Paper text:
{text}
"""

FORMALIZE_CLAIM_PROMPT = """\
Convert this mathematical claim to a Lean 4 theorem with `sorry` proof. \
Use Mathlib4 syntax and imports. Output ONLY the Lean 4 code — no markdown, \
no explanation.

If the claim involves concepts not in Mathlib, define the necessary structures \
first, then state the theorem.

{label}: {statement}
{proof_hint}
"""


def extract_claims_via_llm(
    text: str,
    llm_generate: GenerateFn,
    *,
    max_text_chars: int = 12000,
) -> list[Claim]:
    """Extract claims from unstructured text using an LLM."""
    truncated = _smart_truncate(text, max_text_chars)
    prompt = EXTRACT_CLAIMS_PROMPT.format(text=truncated)

    console.print(f"  Sending {len(truncated):,} chars to LLM for claim extraction...")

    try:
        response = llm_generate(
            "You are a mathematical paper analyst. Extract all theorems precisely.",
            prompt,
        )
    except LLMError as e:
        error_msg = str(e).lower()
        if "timeout" in error_msg or "timed out" in error_msg:
            console.print("[yellow]LLM timed out. Try --pages to limit input, or use a faster model.[/]")
        else:
            console.print(f"[red]LLM error: {e}[/]")
        return []

    return _parse_claims_from_llm(response.text)


def _smart_truncate(text: str, max_chars: int) -> str:
    """Truncate text, preferring theorem-dense sections."""
    if len(text) <= max_chars:
        return text

    first_portion = int(max_chars * 0.4)
    first_part = text[:first_portion]

    remaining = text[first_portion:]
    theorem_keywords = [
        "theorem",
        "lemma",
        "proposition",
        "corollary",
        "conjecture",
        "definition",
    ]

    paragraphs = remaining.split("\n\n")
    theorem_paragraphs = []
    other_paragraphs = []

    for para in paragraphs:
        if any(kw in para.lower() for kw in theorem_keywords):
            theorem_paragraphs.append(para)
        else:
            other_paragraphs.append(para)

    rest_budget = max_chars - first_portion
    rest_parts = []
    used = 0

    for para in theorem_paragraphs + other_paragraphs:
        if used + len(para) + 2 > rest_budget:
            break
        rest_parts.append(para)
        used += len(para) + 2

    return first_part + "\n\n" + "\n\n".join(rest_parts)


def _parse_claims_from_llm(raw: str) -> list[Claim]:
    """Parse claims from LLM response."""
    claims = []

    # Pattern: N. [Type X.Y]: statement
    for m in re.finditer(
        r"(\d+)\.\s*\[([^\]]+)\]\s*:?\s*(.*?)(?=\n\d+\.\s*\[|$)",
        raw,
        re.DOTALL,
    ):
        label = m.group(2).strip()
        statement = m.group(3).strip()
        lean_name = _to_lean_name(label)
        kind = label.split()[0].lower() if label else "claim"
        if statement:
            claims.append(
                Claim(
                    label=label,
                    statement=statement[:500],
                    lean_name=lean_name,
                    kind=kind,
                )
            )

    if claims:
        return claims

    # Fallback: N. Label: statement
    for m in re.finditer(
        r"(\d+)\.\s*"
        r"((?:Theorem|Lemma|Proposition|Corollary|Claim|Definition|Conjecture)"
        r"(?:\s*[\d.()]+)?)"
        r":?\s*(.*?)(?=\n\d+\.\s*(?:Theorem|Lemma|Prop|Cor|Claim|Def|Conj)|$)",
        raw,
        re.DOTALL | re.IGNORECASE,
    ):
        label = m.group(2).strip()
        statement = m.group(3).strip()
        lean_name = _to_lean_name(label)
        kind = label.split()[0].lower() if label else "claim"
        if statement:
            claims.append(
                Claim(
                    label=label,
                    statement=statement[:500],
                    lean_name=lean_name,
                    kind=kind,
                )
            )

    if claims:
        return claims

    # Last resort: numbered lines
    entries = re.split(r"\n(\d+)\.\s+", "\n" + raw.strip())
    i = 1
    while i + 1 < len(entries):
        num = entries[i]
        content = entries[i + 1].strip()
        label_match = re.match(r"\*{0,2}([\w\s.()-]+?)\*{0,2}\s*[:.]?\s*(.*)", content, re.DOTALL)
        if label_match:
            label = label_match.group(1).strip().strip("*")
            statement = label_match.group(2).strip()
            if len(label) > 50:
                label = f"Claim {num}"
                statement = content
        else:
            label = f"Claim {num}"
            statement = content

        if statement:
            claims.append(
                Claim(
                    label=label,
                    statement=statement[:500],
                    lean_name=_to_lean_name(label),
                )
            )
        i += 2

    return claims


# ---------------------------------------------------------------------------
# Unified extraction pipeline
# ---------------------------------------------------------------------------


def read_paper(
    source: str,
    pages: str | None = None,
) -> tuple[list[Claim], str]:
    """Read a paper and extract claims — the main entry point.

    Tries strategies in order:
      1. arXiv native HTML — structured theorem extraction (best)
      2. LLM extraction from PDF text (fallback)
      3. LLM extraction from arXiv abstract (minimal)

    Returns:
        (claims, paper_title) — claims may be empty if all strategies fail.
        If claims are extracted from HTML, they have label+statement+proof_sketch.
        If from LLM, they have label+statement only.
    """
    arxiv_id = _extract_arxiv_id(source)

    if arxiv_id:
        paper_title = f"arXiv:{arxiv_id}"

        # Strategy 1: Structured HTML extraction (no LLM needed)
        console.print("[bold]Strategy 1:[/] arXiv HTML structured extraction")
        claims = extract_claims_from_html(arxiv_id)
        if claims:
            return claims, paper_title

        # Strategy 2: PDF + LLM extraction
        console.print("[bold]Strategy 2:[/] PDF download + LLM extraction")
        try:
            pdf_path = fetch_arxiv(source)
            text = read_pdf(pdf_path, pages=pages)
            if text.strip():
                return [], paper_title  # return empty claims + title; caller uses LLM
        except (OSError, ValueError, RuntimeError, ImportError, httpx.HTTPError) as e:
            console.print(f"  [yellow]PDF failed: {e}[/]")

        # Strategy 3: arXiv abstract
        console.print("[bold]Strategy 3:[/] arXiv API abstract")
        abstract = _fetch_arxiv_abstract(arxiv_id)
        if abstract:
            return [], paper_title

        return [], paper_title

    # Local PDF file
    source_path = Path(source)
    if source_path.exists() and source_path.suffix == ".pdf":
        paper_title = source_path.stem
        return [], paper_title  # caller handles LLM extraction

    console.print(f"[red]Cannot resolve source: {source}[/]")
    return [], "Unknown"


def read_paper_text(source: str, pages: str | None = None) -> tuple[str, str]:
    """Read raw paper text for LLM-based extraction (fallback path).

    Returns (text, paper_title).
    """
    arxiv_id = _extract_arxiv_id(source)

    if arxiv_id:
        paper_title = f"arXiv:{arxiv_id}"
        try:
            pdf_path = fetch_arxiv(source)
            text = read_pdf(pdf_path, pages=pages)
            if text.strip():
                return text, paper_title
        except (OSError, ValueError, RuntimeError, ImportError, httpx.HTTPError) as e:
            console.print(f"  [yellow]PDF failed: {e}[/]")

        abstract = _fetch_arxiv_abstract(arxiv_id)
        if abstract:
            return abstract, paper_title
        return "", paper_title

    source_path = Path(source)
    if source_path.exists() and source_path.suffix == ".pdf":
        return read_pdf(source_path, pages=pages), source_path.stem

    return "", "Unknown"


def _fetch_arxiv_abstract(arxiv_id: str) -> str | None:
    """Fetch paper abstract from the arXiv API."""
    api_url = f"https://export.arxiv.org/api/query?id_list={arxiv_id}"
    try:
        resp = httpx.get(api_url, timeout=30.0)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        namespace = {"atom": "http://www.w3.org/2005/Atom"}
        entry = root.find("atom:entry", namespace)
        if entry is None:
            return None
        title = " ".join((entry.findtext("atom:title", "Unknown", namespace)).split())
        summary = " ".join((entry.findtext("atom:summary", "", namespace)).split())
        authors = [
            " ".join(name.text.split())
            for name in entry.findall("atom:author/atom:name", namespace)
            if name.text
        ]

        if summary:
            text = f"Title: {title}\nAuthors: {', '.join(authors[:5])}\n\nAbstract:\n{summary}"
            console.print(f"  [green]Got abstract ({len(summary)} chars)[/]")
            return text

    except (httpx.HTTPError, ET.ParseError) as e:
        console.print(f"  [yellow]arXiv API failed: {e}[/]")

    return None


# ---------------------------------------------------------------------------
# Formalization
# ---------------------------------------------------------------------------


def formalize_claim(
    claim: Claim,
    llm_generate: GenerateFn,
    system: str = "You are a Lean 4 formalization expert using Mathlib4.",
) -> Claim:
    """Formalize a single claim into Lean 4 code."""
    # Include proof sketch if available — helps the LLM formalize
    proof_hint = ""
    if claim.proof_sketch:
        proof_hint = f"\nProof sketch from paper: {claim.proof_sketch[:300]}"

    prompt = FORMALIZE_CLAIM_PROMPT.format(
        label=claim.label,
        statement=claim.statement,
        proof_hint=proof_hint,
    )

    try:
        response = llm_generate(system, prompt)
        code = re.sub(r"^```(?:lean4?|)\s*\n?", "", response.text.strip())
        code = re.sub(r"\n?```\s*$", "", code)
        code = "\n".join(
            line for line in code.splitlines() if not line.strip().startswith(("import ", "-- import"))
        )
        claim.lean_code = validate_generated_declarations(code)
    except (LLMError, GeneratedCodeError) as e:
        console.print(f"  [yellow]Formalization failed for {claim.label}: {e}[/]")
    return claim


# ---------------------------------------------------------------------------
# Lean file generation
# ---------------------------------------------------------------------------


def render_verification_source(
    claims: list[Claim],
    paper_title: str = "Unknown Paper",
) -> str:
    """Render complete Lean source for formalized paper claims."""
    safe_title = safe_lean_comment_text(paper_title)
    parts = [
        "/-!",
        f"# Verification: {safe_title}",
        "",
        "Auto-generated from paper by AutoLean verify.",
        "Each theorem corresponds to a claim in the paper.",
        "Pending theorems contain explicit sorry targets for the agent.",
    ]
    inputs = sorted({(claim.input_ref, claim.input_sha256) for claim in claims if claim.input_sha256})
    for reference, digest in inputs:
        parts.append(f"Extractor input: {safe_lean_comment_text(reference or 'inline')}")
        parts.append(f"Extractor input SHA-256: {digest}")
    parts.extend(["-/", "", "import Mathlib", ""])

    for c in claims:
        label = safe_lean_comment_text(c.label)
        statement = safe_lean_comment_text(c.statement)
        parts.append(f"-- [{label}]: {statement[:120]}")
        if c.proof_sketch:
            sketch = safe_lean_comment_text(c.proof_sketch)
            parts.append(f"-- Proof sketch: {sketch[:100]}...")

        if c.lean_code:
            validated_code = validate_generated_declarations(c.lean_code)
            code_lines = [
                line
                for line in validated_code.split("\n")
                if not line.strip().startswith(("import ", "-- import"))
            ]
            parts.append("\n".join(code_lines))
        else:
            lean_name = safe_lean_comment_text(c.lean_name)
            parts.append(f"-- Formalization pending: {statement[:80]}")
            parts.append(f"-- theorem {lean_name} : sorry := sorry")
        parts.append("")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Paper structure analysis
# ---------------------------------------------------------------------------


def analyze_paper_structure(claims: list[Claim]) -> dict[str, Any]:
    """Analyze the structure of extracted claims.

    Returns a summary dict with counts by kind, proof coverage, etc.
    """
    by_kind: dict[str, int] = {}
    with_proof = 0
    for c in claims:
        kind = c.kind or "unknown"
        by_kind[kind] = by_kind.get(kind, 0) + 1
        if c.proof_sketch:
            with_proof += 1

    return {
        "total_claims": len(claims),
        "by_kind": by_kind,
        "with_proof": with_proof,
        "provable": [c for c in claims if c.kind in ("theorem", "lemma", "proposition", "corollary")],
        "definitions": [c for c in claims if c.kind == "definition"],
        "remarks": [c for c in claims if c.kind == "remark"],
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_lean_name(label: str) -> str:
    """Convert a theorem label to a Lean identifier."""
    name = label.lower()
    name = re.sub(r"[^a-z0-9]+", "_", name)
    name = name.strip("_")
    return name or "claim"
