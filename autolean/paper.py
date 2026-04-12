"""Paper verification — extract claims from PDFs and formalize in Lean 4.

Workflow:
  1. Read a math paper (PDF)
  2. LLM extracts theorem statements and claims
  3. LLM formalizes each claim as a Lean 4 theorem with sorry
  4. Writes a .lean file into the workspace
  5. The normal agent loop attempts proofs
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

console = Console()


@dataclass
class Claim:
    """A mathematical claim extracted from a paper."""

    label: str  # "Theorem 3.1", "Lemma 2", "Proposition 4.5"
    statement: str  # Natural language statement
    lean_name: str = ""  # Generated Lean identifier
    lean_code: str = ""  # Formalized Lean 4 code


# ---------------------------------------------------------------------------
# PDF extraction
# ---------------------------------------------------------------------------


def fetch_arxiv(arxiv_id_or_url: str, output_dir: Path | None = None) -> Path:
    """Download a paper PDF from arXiv.

    Accepts:
        - arXiv ID: "2404.12534"
        - arXiv URL: "https://arxiv.org/abs/2404.12534"
        - arXiv PDF URL: "https://arxiv.org/pdf/2404.12534"

    Returns:
        Path to the downloaded PDF.
    """
    # Extract arXiv ID from URL
    arxiv_id = arxiv_id_or_url.strip()
    for prefix in ["https://arxiv.org/abs/", "https://arxiv.org/pdf/",
                    "http://arxiv.org/abs/", "http://arxiv.org/pdf/"]:
        if arxiv_id.startswith(prefix):
            arxiv_id = arxiv_id[len(prefix):].rstrip("/").removesuffix(".pdf")
            break

    pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
    output_dir = output_dir or Path("/tmp")
    output_path = output_dir / f"arxiv_{arxiv_id.replace('/', '_')}.pdf"

    if output_path.exists():
        console.print(f"  [dim]Using cached: {output_path}[/]")
        return output_path

    console.print(f"  Downloading [cyan]{pdf_url}[/]...")
    import httpx
    with httpx.stream("GET", pdf_url, follow_redirects=True, timeout=60.0) as resp:
        resp.raise_for_status()
        with open(output_path, "wb") as f:
            for chunk in resp.iter_bytes():
                f.write(chunk)

    size_mb = output_path.stat().st_size / 1024 / 1024
    console.print(f"  [green]Downloaded[/] ({size_mb:.1f} MB)")
    return output_path


def read_pdf(path: Path, pages: str | None = None) -> str:
    """Extract text from a PDF file.

    Requires pymupdf: `uv pip install pymupdf`

    Args:
        path: Path to PDF file.
        pages: Page range string (e.g., "1-5", "3,7,10-12"). None = all pages.

    Returns:
        Extracted text content.
    """
    try:
        import fitz  # pymupdf
    except ImportError:
        raise ImportError(
            "pymupdf is required for PDF reading.\n"
            "Install it with: uv pip install pymupdf"
        )

    doc = fitz.open(str(path))

    # Parse page range
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
            text_parts.append(f"--- Page {i + 1} ---\n{page.get_text()}")

    doc.close()
    return "\n\n".join(text_parts)


# ---------------------------------------------------------------------------
# Claim extraction (LLM-assisted)
# ---------------------------------------------------------------------------

EXTRACT_CLAIMS_PROMPT = """\
List ALL theorems, lemmas, propositions, conjectures, and problems from this paper.
For each, write: N. Label: precise mathematical statement.

Paper:
{text}
"""

FORMALIZE_CLAIM_PROMPT = """\
Convert to a Lean 4 theorem with `sorry` proof. Use Mathlib syntax. \
Output ONLY the Lean 4 code, no markdown.

{label}: {statement}
"""


def extract_claims(
    text: str,
    llm_generate: object,
    system: str = "",
) -> list[Claim]:
    """Extract mathematical claims from paper text using an LLM.

    Args:
        text: Paper text content.
        llm_generate: Callable with signature (system, user) -> response with .text

    Returns:
        List of extracted claims.
    """
    # Keep prompt short and text chunked — thinking models blow budget on long inputs.
    # Use minimal system message to leave token budget for the actual extraction.
    prompt = EXTRACT_CLAIMS_PROMPT.format(text=text[:10000])

    response = llm_generate(system or "List math claims.", prompt)  # type: ignore
    raw = response.text

    # Parse numbered list — flexible format
    claims = []

    # Strategy 1: Look for "N. Label: statement" pattern
    for m in re.finditer(
        r"(\d+)\.\s*"
        r"((?:Theorem|Lemma|Proposition|Corollary|Claim|Definition|Conjecture|Property|Fact)"
        r"(?:\s*[\d.()]+)?)"
        r":?\s*(.*?)(?=\n\d+\.\s*(?:Theorem|Lemma|Prop|Cor|Claim|Def|Conj)|$)",
        raw, re.DOTALL | re.IGNORECASE,
    ):
        label = m.group(2).strip()
        statement = m.group(3).strip()
        lean_name = _to_lean_name(label)
        if statement:  # skip empty matches
            claims.append(Claim(label=label, statement=statement, lean_name=lean_name))

    # Strategy 2: Flexible line-by-line parsing
    if not claims:
        # Split on "N. " at start of line
        entries = re.split(r"\n(\d+)\.\s+", "\n" + raw.strip())
        # entries = ['', '1', 'content1', '2', 'content2', ...]
        i = 1
        while i + 1 < len(entries):
            num = entries[i]
            content = entries[i + 1].strip()
            # Extract label from **Label**: or Label: prefix
            label_match = re.match(
                r"\*{0,2}([\w\s.()-]+?)\*{0,2}\s*[:.]?\s*(.*)",
                content, re.DOTALL,
            )
            if label_match:
                label = label_match.group(1).strip().strip("*")
                statement = label_match.group(2).strip()
                if len(label) > 50:
                    # Label too long — treat whole thing as statement
                    label = f"Claim {num}"
                    statement = content
            else:
                label = f"Claim {num}"
                statement = content

            lean_name = _to_lean_name(label)
            # Truncate very long statements
            statement = statement[:500]
            if statement:
                claims.append(Claim(
                    label=label, statement=statement, lean_name=lean_name,
                ))
            i += 2

    return claims


def formalize_claim(
    claim: Claim,
    llm_generate: object,
    system: str = "You are a Lean 4 formalization expert.",
) -> Claim:
    """Formalize a single claim into Lean 4 code.

    Args:
        claim: The claim to formalize.
        llm_generate: Callable with signature (system, user) -> response with .text

    Returns:
        Updated claim with lean_code filled in.
    """
    prompt = FORMALIZE_CLAIM_PROMPT.format(
        label=claim.label,
        statement=claim.statement,
    )

    response = llm_generate(system, prompt)  # type: ignore
    code = response.text.strip()

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
    """Write a .lean file with sorry'd formalizations of paper claims.

    Args:
        claims: List of formalized claims.
        output_path: Where to write the .lean file.
        paper_title: Paper title for the file header.

    Returns:
        Path to the generated file.
    """
    parts = [
        f"/-!",
        f"# Verification: {paper_title}",
        f"",
        f"Auto-generated from paper by AutoLean verify-paper.",
        f"Each theorem statement corresponds to a claim in the paper.",
        f"Proofs are sorry — the agent will attempt them.",
        f"-/",
        f"",
    ]

    # Collect unique imports from claim code
    imports = set()
    for c in claims:
        for line in c.lean_code.split("\n"):
            if line.strip().startswith("import ") or line.strip().startswith("-- import"):
                imports.add(line.strip().removeprefix("-- "))

    for imp in sorted(imports):
        parts.append(imp)
    if imports:
        parts.append("")

    # Add each claim
    for i, c in enumerate(claims, 1):
        parts.append(f"-- [{c.label}]: {c.statement[:100]}")
        if c.lean_code:
            # Filter out import lines (already at top)
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
# Helpers
# ---------------------------------------------------------------------------


def _to_lean_name(label: str) -> str:
    """Convert a theorem label to a Lean identifier.

    'Theorem 3.1' -> 'theorem_3_1'
    'Lemma 2' -> 'lemma_2'
    """
    name = label.lower()
    name = re.sub(r"[^a-z0-9]+", "_", name)
    name = name.strip("_")
    return name or "claim"
