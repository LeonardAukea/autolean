"""Readiness records for paper and indexed-code research tools."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version

from autolean.code_search import CodeDBSearchProvider
from autolean.paper import lightpanda_identity


@dataclass(frozen=True)
class ResearchTool:
    """One observable research-tool capability."""

    name: str
    identity: str
    available: bool
    required: bool = False


def research_tools() -> tuple[ResearchTool, ...]:
    """Inspect the tools that supply paper and code context."""
    try:
        pdf_identity = f"PyMuPDF4LLM {version('pymupdf4llm')}"
        pdf_available = True
    except PackageNotFoundError:
        pdf_identity = "PyMuPDF4LLM unavailable"
        pdf_available = False

    lightpanda = lightpanda_identity()
    codedb = CodeDBSearchProvider().identity()
    return (
        ResearchTool("PDF extraction", pdf_identity, pdf_available, required=True),
        ResearchTool("browser extraction", lightpanda, not lightpanda.endswith("unavailable")),
        ResearchTool("code search", codedb, not codedb.endswith("unavailable")),
    )
