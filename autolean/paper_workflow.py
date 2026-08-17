"""Interactive paper verification orchestration."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import click
from rich.console import Console
from rich.text import Text

import autolean.paper as paper_api
from autolean.lean_interface import BuildResult, LeanProject
from autolean.llm import LLMBackend
from autolean.paper import (
    Claim,
    PaperArtifact,
    PdfEngine,
    PreparedPaper,
)
from autolean.paper_evidence import (
    analyze_paper_structure,
    bind_reviewed_paper,
    mark_reviewed_paper_elaborated,
    render_verification_source,
    write_paper_coverage,
    write_paper_plan,
)
from autolean.paper_profiles import PaperProfile, PaperProfileError
from autolean.program import ProgramConfig, parse_program
from autolean.provenance import sha256_text
from autolean.strategy import PlanAttempt, ProofPlan
from autolean.ui import GLYPH_OK, GLYPH_SKIP


class ConnectLLM(Protocol):
    """Build and preflight one configured model backend."""

    def __call__(
        self,
        model: str | None,
        backend: str | None,
        program_config: ProgramConfig,
        *,
        timeout: float | None = None,
    ) -> LLMBackend: ...


class PlanProof(Protocol):
    """Build a validated proof strategy from one model backend."""

    def __call__(
        self,
        statement: str,
        llm: LLMBackend,
        guidance: tuple[str, ...],
        *,
        context: str = "",
        on_response: Callable[[PlanAttempt], None] | None = None,
    ) -> ProofPlan: ...


class AcceptSource(Protocol):
    """Validate and install one generated Lean source."""

    def __call__(
        self,
        lean_root: Path,
        output: Path,
        content: str,
        *,
        timeout: int = 120,
        expected_content: str | None = None,
    ) -> tuple[Path, BuildResult]: ...


@dataclass(frozen=True)
class PaperServices:
    """CLI-owned effects used by the paper workflow."""

    console: Console
    connect_llm: ConnectLLM
    plan_proof: PlanProof
    show_plan: Callable[[ProofPlan], None]
    accept_source: AcceptSource


def _reviewed_plan_context(profile: PaperProfile | None) -> list[str]:
    if profile is None:
        return []
    return [
        f"Reviewed profile: {profile.id}",
        f"PDF SHA-256: {profile.pdf_sha256}",
        "Executable evidence: one closed abbreviation per mapping edge, sandboxed "
        "Lean elaboration, a coverage-v2 ledger, a durable session, and an export.",
        "Evidence boundaries: no separate #check log, signature capture, mapping "
        "grade, paper-form equivalence proof, or per-declaration axiom audit.",
        "The following mappings are reviewed premises. Plan only the executable "
        "audit and list stronger evidence as follow-up work.",
    ]


def _plan_context(claims: list[Claim], profile: PaperProfile | None) -> str:
    lines = _reviewed_plan_context(profile)
    for claim in claims:
        mapping = f"; reviewed Lean: {', '.join(claim.lean_declarations)}" if claim.lean_declarations else ""
        lines.append(f"- {claim.label}: {claim.statement[:500]}{mapping}")
    return "\n".join(lines)


def _display_claims(console: Console, claims: list[Claim]) -> dict[str, Any]:
    structure = analyze_paper_structure(claims)
    console.print(f"\n[bold]Found {len(claims)} mathematical items:[/]")
    for kind, count in sorted(structure["by_kind"].items()):
        console.print(f"  {kind}: {count}")
    console.print("\n[bold]Workflow:[/]")
    for disposition, count in structure["by_disposition"].items():
        console.print(f"  {disposition}: {count}")
    console.print()
    for index, claim in enumerate(claims, 1):
        proof_marker = " [dim](has proof)[/]" if claim.proof_sketch else ""
        console.print(
            f"  {index}. [bold]{claim.label}[/] "
            f"[{claim.disposition.value}]: {claim.statement[:100]}...{proof_marker}"
        )
    return structure


def _review_plan(
    services: PaperServices,
    paper_title: str,
    formalizer: LLMBackend,
    guidance: tuple[str, ...],
    context: str,
    *,
    interactive: bool,
) -> tuple[ProofPlan, tuple[PlanAttempt, ...]]:
    responses: list[PlanAttempt] = []
    current_guidance = guidance
    plan = services.plan_proof(
        f"Verify the formal claims in {paper_title}",
        formalizer,
        current_guidance,
        context=context,
        on_response=responses.append,
    )
    services.show_plan(plan)
    while interactive and not click.confirm("Use this paper plan?", default=True):
        revision = click.prompt("Additional guidance", type=str).strip()
        current_guidance = (*current_guidance, revision)
        plan = services.plan_proof(
            f"Verify the formal claims in {paper_title}",
            formalizer,
            current_guidance,
            context=context,
            on_response=responses.append,
        )
        services.show_plan(plan)
    return plan, tuple(responses)


def _output_path(
    lean_root: Path,
    output: Path | None,
    profile: PaperProfile | None,
    paper_title: str,
) -> Path:
    identity = profile.id if profile is not None else paper_title
    safe_title = re.sub(r"[^a-zA-Z0-9_]", "_", identity or "Untitled")
    if output is None:
        return lean_root / "AutoLean" / f"Paper_{safe_title}.lean"
    return output if output.is_absolute() else lean_root / output


def _display_extraction(
    console: Console,
    artifact: PaperArtifact,
    claims: list[Claim],
    coverage_path: Path | None,
) -> None:
    console.print(
        f"[green]Extracted paper artifact[/]\n"
        f"  Markdown: {artifact.markdown_path}\n"
        f"  PDF:      {artifact.pdf_path or 'not available'}\n"
        f"  Source:   sha256:{artifact.input_sha256}\n"
        f"  PDF:      sha256:{artifact.pdf_sha256 or 'not available'}\n"
        f"  Text:     sha256:{artifact.text_sha256}\n"
        f"  Coverage: {coverage_path or 'requires claim extraction'}"
    )
    if claims:
        console.print(f"\n[bold]Found {len(claims)} structured claims:[/]")
        for index, claim in enumerate(claims, 1):
            console.print(f"  {index}. [bold]{claim.label}[/]: {claim.statement[:100]}")


def prepare_paper(
    source: str,
    *,
    pages: str | None,
    pdf_engine: str,
    paddleocr_url: str | None,
    extract_only: bool,
    output: Path | None,
    model: str | None,
    backend: str | None,
    guide: tuple[str, ...],
    review_plan: bool,
    program: Path,
    services: PaperServices,
) -> tuple[PreparedPaper | None, ProgramConfig]:
    """Extract, review, formalize, and accept one paper artifact."""
    cfg = parse_program(program)
    llm: LLMBackend | None = None
    extracted_input_sha256 = ""

    def connected_llm() -> LLMBackend:
        nonlocal llm
        if llm is None:
            llm = services.connect_llm(model, backend, cfg, timeout=600.0)
        return llm

    services.console.print(f"[bold]Analyzing paper: {source}[/]\n")
    try:
        try:
            document = paper_api.read_paper(
                source,
                pages=pages,
                pdf_engine=PdfEngine(pdf_engine),
                paddleocr_url=paddleocr_url,
            )
        except (OSError, ValueError, RuntimeError) as error:
            raise click.ClickException(f"Paper extraction failed: {error}") from error

        lean_root = program.parent / cfg.lean_project_path
        try:
            paper_artifact = paper_api.materialize_paper(document, lean_root)
        except (OSError, ValueError) as error:
            raise click.ClickException(f"Paper artifact could not be saved: {error}") from error
        if paper_artifact.pdf_path is not None:
            document.pdf_path = paper_artifact.pdf_path
        try:
            reviewed_profile = bind_reviewed_paper(document.claims, paper_artifact)
        except PaperProfileError as error:
            raise click.ClickException(f"Reviewed paper profile failed: {error}") from error
        if reviewed_profile is not None:
            document.title = reviewed_profile.title
            services.console.print(
                f"[green]Reviewed profile:[/] {reviewed_profile.id} · "
                f"{len(reviewed_profile.items)} numbered items"
            )

        if extract_only:
            coverage_path = write_paper_coverage(paper_artifact, document.claims) if document.claims else None
            _display_extraction(
                services.console,
                paper_artifact,
                document.claims,
                coverage_path,
            )
            return None, cfg

        claims = document.claims
        paper_title = document.title
        if not claims:
            services.console.print("[bold]Using model-based extraction fallback...[/]")
            if document.text and len(document.text.strip()) > 100:
                extracted_input_sha256 = document.input_sha256
                claims = paper_api.extract_document_claims(document, connected_llm())

        if not claims:
            raise click.ClickException("No claims were extracted; select a page range or another source.")
        if extracted_input_sha256:
            for claim in claims:
                claim.input_ref = document.input_ref
                claim.input_sha256 = extracted_input_sha256

        if document.extractor:
            services.console.print(
                f"[dim]Extractor: {document.extractor} · "
                f"sha256:{document.input_sha256[:16] or 'unavailable'}[/]"
            )

        structure = _display_claims(services.console, claims)
        to_formalize = structure["proof_obligations"]
        if not isinstance(to_formalize, list) or not to_formalize:
            raise click.ClickException("The paper contains no extracted proof obligations.")
        formalizer = connected_llm()
        proof_plan, responses = _review_plan(
            services,
            paper_title,
            formalizer,
            guide,
            _plan_context(claims, reviewed_profile),
            interactive=review_plan,
        )
        plan_path = write_paper_plan(
            paper_artifact,
            proof_plan,
            model=formalizer.config.model,
            backend=formalizer.config.backend,
            responses=responses,
        )
        services.console.print(f"[dim]Accepted plan:[/] sha256:{proof_plan.sha256} · {plan_path}")

        if reviewed_profile is not None:
            services.console.print(
                f"\n[bold]Binding {len(claims)} reviewed items to the pinned Lean closure...[/]"
            )
            for claim in claims:
                names = ", ".join(claim.lean_declarations)
                services.console.print(
                    f"  [green]{GLYPH_OK}[/] ",
                    Text(f"{claim.label} → {names}"),
                    sep="",
                )
        else:
            services.console.print(f"\n[bold]Formalizing {len(to_formalize)} proof obligations...[/]")
            for claim in to_formalize:
                with services.console.status(f"[dim]Formalizing {claim.label}..."):
                    paper_api.formalize_claim(claim, formalizer.generate)
                if claim.lean_code:
                    services.console.print(f"  [green]{GLYPH_OK}[/] {claim.label} -> {claim.lean_name}")
                else:
                    services.console.print(f"  [yellow]{GLYPH_SKIP}[/] {claim.label}")

        formalized = sum(bool(claim.lean_code) for claim in claims)
        if formalized == 0:
            raise click.ClickException("No claims could be formalized.")

        output_path = _output_path(lean_root, output, reviewed_profile, paper_title)
        content = render_verification_source(
            claims,
            paper_title=paper_title,
            imports=reviewed_profile.imports if reviewed_profile is not None else ("Mathlib",),
        )
        expected_content = None
        if output_path.exists():
            current = output_path.read_text(encoding="utf-8")
            if current != content:
                raise click.ClickException(f"Paper evidence differs from the existing source: {output_path}")
            expected_content = current
        output_path, acceptance = services.accept_source(
            lean_root,
            output_path,
            content,
            timeout=300,
            expected_content=expected_content,
        )
        if reviewed_profile is not None:
            mark_reviewed_paper_elaborated(claims, reviewed_profile)

        proof_environment = LeanProject(lean_root).proof_environment(refresh=True).as_dict()
        lean_evidence = {
            "declaration_count": sum(len(claim.evidence_names) for claim in claims),
            "duration_seconds": acceptance.duration_seconds,
            "error_count": len(acceptance.errors),
            "module": output_path.resolve().relative_to(lean_root.resolve()).as_posix(),
            "source_sha256": sha256_text(content),
            "success": acceptance.success,
            "warning_count": len(acceptance.warnings),
        }
        coverage_path = write_paper_coverage(
            paper_artifact,
            claims,
            plan_path=plan_path,
            proof_environment=proof_environment,
            lean_evidence=lean_evidence,
        )
        services.console.print(f"\n[bold green]Accepted {output_path}[/]")
        if reviewed_profile is not None:
            services.console.print(f"  {len(claims)} paper items passed Lean elaboration")
        else:
            services.console.print(f"  {formalized} declarations ready for proving")
        services.console.print(f"  Coverage: {coverage_path}")
        services.console.print(f"  Source:   sha256:{paper_artifact.input_sha256}")
        if paper_artifact.pdf_sha256:
            services.console.print(f"  PDF:      sha256:{paper_artifact.pdf_sha256}")
        return (
            PreparedPaper(
                lean_path=output_path,
                source=paper_artifact,
                coverage_path=coverage_path,
                plan_path=plan_path,
                profile_id=reviewed_profile.id if reviewed_profile is not None else "",
                model=formalizer.config.model,
                backend=formalizer.config.backend,
            ),
            cfg,
        )
    finally:
        if llm is not None:
            llm.close()
