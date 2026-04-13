"""Paper verification — extract claims from PDFs and formalize in Lean 4.

Workflow:
  1. Read a math paper (arXiv HTML, PDF, or abstract)
  2. Extract theorem/lemma/definition environments (structured HTML or LLM)
  3. LLM formalizes each claim as a Lean 4 theorem with sorry
  4. Writes a .lean file into the workspace
  5. The normal agent loop attempts proofs

Text extraction strategies (tried in order):
  1. arXiv native HTML — structured theorem environments, proofs, math
  2. pymupdf — local PDF text extraction (fallback for non-arXiv)
  3. arXiv API abstract — minimal but always works
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from rich.console import Console

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


# ---------------------------------------------------------------------------
# arXiv helpers
# ---------------------------------------------------------------------------


def _extract_arxiv_id(source: str) -> str | None:
    """Extract arXiv ID from a URL or raw ID string."""
    source = source.strip().rstrip("/").removesuffix(".pdf")
    for prefix in [
        "https://arxiv.org/abs/", "https://arxiv.org/pdf/",
        "http://arxiv.org/abs/", "http://arxiv.org/pdf/",
        "https://arxiv.org/html/",
        "https://ar5iv.labs.arxiv.org/html/",
    ]:
        if source.startswith(prefix):
            return source[len(prefix):].split("v")[0]  # strip version suffix

    # Bare ID like "2604.07408" or "math/0411045"
    if re.match(r"^\d{4}\.\d{4,5}(v\d+)?$", source):
        return source.split("v")[0]
    if re.match(r"^[a-z-]+/\d{7}$", source):
        return source

    return None


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
    import httpx
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
    import httpx

    # Try arxiv.org/html/ (native rendering — best structure)
    for url in [
        f"https://arxiv.org/html/{arxiv_id}v1",
        f"https://arxiv.org/html/{arxiv_id}",
    ]:
        console.print(f"  Fetching: [cyan]{url}[/]...")
        try:
            resp = httpx.get(url, follow_redirects=True, timeout=timeout)
            if resp.status_code == 200 and len(resp.text) > 10000:
                claims = _parse_arxiv_html_theorems(resp.text)
                if claims:
                    console.print(
                        f"  [green]Extracted {len(claims)} theorem environments from HTML[/]"
                    )
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
    claims: list[Claim] = []

    # Step 1: Find all theorem blocks and their positions
    theorem_pattern = re.compile(
        r'<div[^>]*class="ltx_theorem\s+ltx_theorem_(\w+)"[^>]*>(.*?)</div>',
        re.DOTALL,
    )
    theorem_blocks = [(m.start(), m.end(), m.group(1), m.group(2))
                      for m in theorem_pattern.finditer(html)]

    # Step 2: Find all proof blocks and their positions
    proof_pattern = re.compile(
        r'<div[^>]*class="ltx_proof"[^>]*>(.*?)</div>',
        re.DOTALL,
    )
    proof_blocks = [(m.start(), m.end(), m.group(1))
                    for m in proof_pattern.finditer(html)]

    # Step 3: Match proofs to theorems (a proof belongs to the closest
    # preceding theorem/lemma/proposition)
    proof_map: dict[int, str] = {}  # theorem_index -> proof_text
    for p_start, p_end, p_text in proof_blocks:
        # Find the closest preceding theorem
        best_idx = -1
        best_dist = float("inf")
        for i, (t_start, t_end, t_kind, t_block) in enumerate(theorem_blocks):
            if t_end <= p_start:
                dist = p_start - t_end
                if dist < best_dist:
                    best_dist = dist
                    best_idx = i
        # Only match if proof is within ~500 chars of theorem end
        if best_idx >= 0 and best_dist < 500:
            proof_text = _strip_html(p_text).strip()
            if proof_text.lower().startswith("proof"):
                proof_text = proof_text[5:].strip().lstrip(".").strip()
            # Remove trailing QED symbol
            proof_text = proof_text.rstrip("∎").strip()
            proof_map[best_idx] = proof_text

    # Step 4: Build claims
    for i, (t_start, t_end, kind, block) in enumerate(theorem_blocks):
        # Extract label from ltx_tag
        tag_match = re.search(
            r'class="ltx_tag[^"]*"[^>]*>(.*?)</span>',
            block, re.DOTALL,
        )
        if tag_match:
            label = _strip_html(tag_match.group(1)).strip().rstrip(".")
        else:
            label = kind.capitalize()

        # Extract statement
        statement = _strip_html(block).strip()
        if statement.lower().startswith(label.lower()):
            statement = statement[len(label):].strip().lstrip(".").strip()

        # Get matched proof
        proof_sketch = proof_map.get(i, "")

        lean_name = _to_lean_name(label)

        if statement and len(statement) > 10:
            claims.append(Claim(
                label=label,
                statement=statement[:1000],
                lean_name=lean_name,
                kind=kind,
                proof_sketch=proof_sketch[:500] if proof_sketch else "",
            ))

    return claims


def _strip_html(text: str) -> str:
    """Remove HTML tags and normalize whitespace, preserving math notation."""
    # Replace <math> content with a placeholder that preserves the alt text
    text = re.sub(r'<math[^>]*alttext="([^"]*)"[^>]*>.*?</math>', r' \1 ', text, flags=re.DOTALL)
    # Replace remaining <math> with inline text
    text = re.sub(r'<math[^>]*>(.*?)</math>', lambda m: _strip_html(m.group(1)), text, flags=re.DOTALL)
    # Strip all remaining tags
    text = re.sub(r'<[^>]+>', ' ', text)
    # Decode HTML entities
    text = text.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
    text = text.replace('&#x27;', "'").replace('&quot;', '"')
    # Normalize whitespace
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


# ---------------------------------------------------------------------------
# PDF extraction (pymupdf fallback)
# ---------------------------------------------------------------------------


def read_pdf(path: Path, pages: str | None = None) -> str:
    """Extract text from a PDF file using pymupdf."""
    try:
        import fitz  # pymupdf
    except ImportError:
        raise ImportError(
            "pymupdf is required for PDF reading.\n"
            "Install it with: uv pip install pymupdf"
        )

    doc = fitz.open(str(path))

    page_indices: list[int] = []
    if pages:
        for part in pages.split(","):
            part = part.strip()
            if "-" in part:
                start, end = part.split("-", 1)
                page_indices.extend(range(int(start) - 1, int(end)))
            else:
                page_indices.append(int(part) - 1)
    else:
        page_indices = list(range(len(doc)))

    text_parts = []
    for i in page_indices:
        if 0 <= i < len(doc):
            page = doc[i]
            text = page.get_text("text", sort=True)
            if text.strip():
                text_parts.append(f"--- Page {i + 1} ---\n{text}")

    doc.close()
    result = "\n\n".join(text_parts)

    if not result.strip():
        console.print(
            "  [yellow]pymupdf extracted no text (scanned PDF?).[/]\n"
            "  Consider using --pages to target specific pages."
        )

    return result


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
    llm_generate: object,
    *,
    max_text_chars: int = 12000,
) -> list[Claim]:
    """Extract claims from unstructured text using an LLM."""
    truncated = _smart_truncate(text, max_text_chars)
    prompt = EXTRACT_CLAIMS_PROMPT.format(text=truncated)

    console.print(f"  Sending {len(truncated):,} chars to LLM for claim extraction...")

    try:
        response = llm_generate(  # type: ignore
            "You are a mathematical paper analyst. Extract all theorems precisely.",
            prompt,
        )
    except Exception as e:
        error_msg = str(e).lower()
        if "timeout" in error_msg or "timed out" in error_msg:
            console.print(
                "[yellow]LLM timed out. Try --pages to limit input, "
                "or use a faster model.[/]"
            )
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
        "theorem", "lemma", "proposition", "corollary", "conjecture", "definition",
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
        raw, re.DOTALL,
    ):
        label = m.group(2).strip()
        statement = m.group(3).strip()
        lean_name = _to_lean_name(label)
        kind = label.split()[0].lower() if label else "claim"
        if statement:
            claims.append(Claim(
                label=label, statement=statement[:500],
                lean_name=lean_name, kind=kind,
            ))

    if claims:
        return claims

    # Fallback: N. Label: statement
    for m in re.finditer(
        r"(\d+)\.\s*"
        r"((?:Theorem|Lemma|Proposition|Corollary|Claim|Definition|Conjecture)"
        r"(?:\s*[\d.()]+)?)"
        r":?\s*(.*?)(?=\n\d+\.\s*(?:Theorem|Lemma|Prop|Cor|Claim|Def|Conj)|$)",
        raw, re.DOTALL | re.IGNORECASE,
    ):
        label = m.group(2).strip()
        statement = m.group(3).strip()
        lean_name = _to_lean_name(label)
        kind = label.split()[0].lower() if label else "claim"
        if statement:
            claims.append(Claim(
                label=label, statement=statement[:500],
                lean_name=lean_name, kind=kind,
            ))

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
            claims.append(Claim(
                label=label, statement=statement[:500],
                lean_name=_to_lean_name(label),
            ))
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
        console.print(f"[bold]Strategy 1:[/] arXiv HTML structured extraction")
        claims = extract_claims_from_html(arxiv_id)
        if claims:
            return claims, paper_title

        # Strategy 2: PDF + LLM extraction
        console.print(f"[bold]Strategy 2:[/] PDF download + LLM extraction")
        try:
            pdf_path = fetch_arxiv(source)
            text = read_pdf(pdf_path, pages=pages)
            if text.strip():
                return [], paper_title  # return empty claims + title; caller uses LLM
        except Exception as e:
            console.print(f"  [yellow]PDF failed: {e}[/]")

        # Strategy 3: arXiv abstract
        console.print(f"[bold]Strategy 3:[/] arXiv API abstract")
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
        except Exception as e:
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
    import httpx

    api_url = f"http://export.arxiv.org/api/query?id_list={arxiv_id}"
    try:
        resp = httpx.get(api_url, timeout=30.0)
        resp.raise_for_status()

        title_match = re.search(r"<title>(.*?)</title>", resp.text, re.DOTALL)
        title = title_match.group(1).strip() if title_match else "Unknown"
        if title == "ArXiv Query:":
            titles = re.findall(r"<title>(.*?)</title>", resp.text, re.DOTALL)
            title = titles[1].strip() if len(titles) > 1 else "Unknown"

        summary_match = re.search(r"<summary>(.*?)</summary>", resp.text, re.DOTALL)
        summary = summary_match.group(1).strip() if summary_match else ""

        authors = re.findall(r"<name>(.*?)</name>", resp.text)

        if summary:
            text = f"Title: {title}\nAuthors: {', '.join(authors[:5])}\n\nAbstract:\n{summary}"
            console.print(f"  [green]Got abstract ({len(summary)} chars)[/]")
            return text

    except Exception as e:
        console.print(f"  [yellow]arXiv API failed: {e}[/]")

    return None


# ---------------------------------------------------------------------------
# Formalization
# ---------------------------------------------------------------------------


def formalize_claim(
    claim: Claim,
    llm_generate: object,
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
        response = llm_generate(system, prompt)  # type: ignore
        code = response.text.strip()
    except Exception as e:
        console.print(f"  [yellow]Formalization failed for {claim.label}: {e}[/]")
        return claim

    # Clean markdown fences
    code = re.sub(r"^```(?:lean4?|)\s*\n?", "", code)
    code = re.sub(r"\n?```\s*$", "", code)

    claim.lean_code = code.strip()
    return claim


# ---------------------------------------------------------------------------
# Lean file generation
# ---------------------------------------------------------------------------


def create_verification_file(
    claims: list[Claim],
    output_path: Path,
    paper_title: str = "Unknown Paper",
) -> Path:
    """Write a .lean file with sorry'd formalizations of paper claims."""
    parts = [
        "/-!",
        f"# Verification: {paper_title}",
        "",
        "Auto-generated from paper by AutoLean verify.",
        "Each theorem corresponds to a claim in the paper.",
        "Proofs are sorry — the agent will attempt them.",
        "-/",
        "",
    ]

    # Collect unique imports
    imports = set()
    for c in claims:
        for line in c.lean_code.split("\n"):
            if line.strip().startswith("import ") or line.strip().startswith("-- import"):
                imports.add(line.strip().removeprefix("-- "))

    for imp in sorted(imports):
        parts.append(imp)
    if imports:
        parts.append("")

    for i, c in enumerate(claims, 1):
        # Header comment with label and statement
        parts.append(f"-- [{c.label}]: {c.statement[:120]}")
        if c.proof_sketch:
            parts.append(f"-- Proof sketch: {c.proof_sketch[:100]}...")

        if c.lean_code:
            code_lines = [
                l for l in c.lean_code.split("\n")
                if not l.strip().startswith("import ") and not l.strip().startswith("-- import")
            ]
            parts.append("\n".join(code_lines))
        else:
            parts.append(f"-- Could not formalize: {c.statement[:80]}")
            parts.append(f"-- theorem {c.lean_name} : sorry := sorry")
        parts.append("")

    output_path.write_text("\n".join(parts), encoding="utf-8")
    return output_path


# ---------------------------------------------------------------------------
# Paper structure analysis
# ---------------------------------------------------------------------------


def analyze_paper_structure(claims: list[Claim]) -> dict:
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
