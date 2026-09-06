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
from autolean.files import read_source
from autolean.lean_interface import BuildResult, LeanProject
from autolean.llm import LLMBackend, inference_location
from autolean.paper import (
    Claim,
    PaperArtifact,
    PaperDocument,
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
from autolean.validation import require_instance, require_optional_text


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

    def __post_init__(self) -> None:
        require_instance(self.console, Console, "paper workflow console must be a Console")
        effects = (
            self.connect_llm,
            self.plan_proof,
            self.show_plan,
            self.accept_source,
        )
        if not all(callable(effect) for effect in effects):
            raise ValueError("paper workflow effects must be callable")


@dataclass
class _PaperRuntime:
    """Own the lazily connected model used by one paper command."""

    config: ProgramConfig
    model: str | None
    backend: str | None
    services: PaperServices
    llm: LLMBackend | None = None

    def __post_init__(self) -> None:
        require_instance(
            self.config,
            ProgramConfig,
            "paper runtime requires a program configuration",
        )
        require_optional_text(self.model, "paper runtime model must not be empty")
        require_optional_text(self.backend, "paper runtime provider must not be empty")
        require_instance(
            self.services,
            PaperServices,
            "paper runtime requires workflow services",
        )
        if self.llm is not None:
            require_instance(self.llm, LLMBackend, "paper runtime model is invalid")

    def connected_llm(self) -> LLMBackend:
        """Return the command's single preflighted model connection."""
        if self.llm is None:
            self.llm = require_instance(
                self.services.connect_llm(
                    self.model,
                    self.backend,
                    self.config,
                    timeout=600.0,
                ),
                LLMBackend,
                "paper model connection returned an invalid backend",
            )
        return self.llm

    def close(self) -> None:
        """Close the model connection when this command opened one."""
        llm = self.llm
        self.llm = None
        if llm is not None:
            llm.close()


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


def _acquire_paper(
    source: str,
    *,
    pages: str | None,
    pdf_engine: str,
    paddleocr_url: str | None,
    lean_root: Path,
    services: PaperServices,
) -> tuple[PaperDocument, PaperArtifact, PaperProfile | None]:
    """Acquire, materialize, and identify one paper source."""
    try:
        document = paper_api.read_paper(
            source,
            pages=pages,
            pdf_engine=PdfEngine(pdf_engine),
            paddleocr_url=paddleocr_url,
        )
    except (OSError, ValueError, RuntimeError) as error:
        raise click.ClickException(f"Paper extraction failed: {error}") from error
    try:
        artifact = paper_api.materialize_paper(document, lean_root)
    except (OSError, ValueError) as error:
        raise click.ClickException(f"Paper artifact could not be saved: {error}") from error
    if artifact.pdf_path is not None:
        document.pdf_path = artifact.pdf_path
    try:
        profile = bind_reviewed_paper(document.claims, artifact)
    except PaperProfileError as error:
        raise click.ClickException(f"Reviewed paper profile failed: {error}") from error
    if profile is not None:
        document.title = profile.title
        services.console.print(
            f"[green]Reviewed profile:[/] {profile.id} · {len(profile.items)} numbered items"
        )
    return document, artifact, profile


def _finish_extraction(
    services: PaperServices,
    document: PaperDocument,
    artifact: PaperArtifact,
) -> None:
    """Persist extraction coverage and display its identities."""
    coverage_path = (
        write_paper_coverage(
            artifact,
            document.claims,
            document_extraction_receipt=document.document_extraction_receipt,
            extraction_receipt=document.extraction_receipt,
        )
        if document.claims
        else None
    )
    _display_extraction(
        services.console,
        artifact,
        document.claims,
        coverage_path,
    )


def _claims_for_verification(
    document: PaperDocument,
    runtime: _PaperRuntime,
) -> list[Claim]:
    """Return structured claims, using the model fallback when needed."""
    claims = document.claims
    model_extracted = False
    if not claims:
        runtime.services.console.print("[bold]Using model-based extraction fallback...[/]")
        if document.text and len(document.text.strip()) > 100:
            claims = paper_api.extract_document_claims(document, runtime.connected_llm())
            model_extracted = True
    if not claims:
        raise click.ClickException("No claims were extracted; select a page range or another source.")
    if model_extracted:
        for claim in claims:
            claim.input_ref = document.input_ref
            claim.input_sha256 = document.input_sha256
    if document.extractor:
        runtime.services.console.print(
            f"[dim]Extractor: {document.extractor} · sha256:{document.input_sha256[:16] or 'unavailable'}[/]"
        )
    return claims


def _plan_paper(
    runtime: _PaperRuntime,
    claims: list[Claim],
    profile: PaperProfile | None,
    artifact: PaperArtifact,
    *,
    paper_title: str,
    guidance: tuple[str, ...],
    review_plan: bool,
) -> tuple[LLMBackend, Path, list[Claim]]:
    """Plan the executable obligations and persist the accepted plan."""
    structure = _display_claims(runtime.services.console, claims)
    obligations = structure["proof_obligations"]
    if not isinstance(obligations, list) or not obligations:
        raise click.ClickException("The paper contains no extracted proof obligations.")
    formalizer = runtime.connected_llm()
    proof_plan, responses = _review_plan(
        runtime.services,
        paper_title,
        formalizer,
        guidance,
        _plan_context(claims, profile),
        interactive=review_plan,
    )
    plan_path = write_paper_plan(
        artifact,
        proof_plan,
        model=formalizer.config.model,
        backend=formalizer.config.backend,
        location=inference_location(formalizer.config),
        responses=responses,
    )
    runtime.services.console.print(f"[dim]Accepted plan:[/] sha256:{proof_plan.sha256} · {plan_path}")
    return formalizer, plan_path, obligations


def _formalize_paper_claims(
    services: PaperServices,
    claims: list[Claim],
    obligations: list[Claim],
    profile: PaperProfile | None,
    formalizer: LLMBackend,
) -> int:
    """Bind reviewed claims or model-formalize extracted proof obligations."""
    if profile is not None:
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
        services.console.print(f"\n[bold]Formalizing {len(obligations)} proof obligations...[/]")
        for claim in obligations:
            with services.console.status(f"[dim]Formalizing {claim.label}..."):
                paper_api.formalize_claim(claim, formalizer)
            marker = GLYPH_OK if claim.lean_code else GLYPH_SKIP
            color = "green" if claim.lean_code else "yellow"
            detail = f" -> {claim.lean_name}" if claim.lean_code else ""
            services.console.print(f"  [{color}]{marker}[/] {claim.label}{detail}")
    formalized = sum(bool(claim.lean_code) for claim in claims)
    if formalized == 0:
        raise click.ClickException("No claims could be formalized.")
    return formalized


def _accept_paper_source(
    services: PaperServices,
    lean_root: Path,
    output: Path | None,
    claims: list[Claim],
    profile: PaperProfile | None,
    *,
    paper_title: str,
) -> tuple[Path, BuildResult, str]:
    """Render and atomically accept the paper's Lean evidence source."""
    output_path = _output_path(lean_root, output, profile, paper_title)
    content = render_verification_source(
        claims,
        paper_title=paper_title,
        imports=profile.imports if profile is not None else ("Mathlib",),
    )
    expected_content = None
    if output_path.exists():
        current = read_source(output_path)
        if current != content:
            raise click.ClickException(f"Paper evidence differs from the existing source: {output_path}")
        expected_content = current
    accepted_path, acceptance = services.accept_source(
        lean_root,
        output_path,
        content,
        timeout=300,
        expected_content=expected_content,
    )
    if profile is not None:
        mark_reviewed_paper_elaborated(claims, profile)
    return accepted_path, acceptance, content


def _write_acceptance_coverage(
    lean_root: Path,
    document: PaperDocument,
    artifact: PaperArtifact,
    claims: list[Claim],
    plan_path: Path,
    output_path: Path,
    acceptance: BuildResult,
    content: str,
) -> Path:
    """Persist the complete kernel and model evidence ledger."""
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
    return write_paper_coverage(
        artifact,
        claims,
        plan_path=plan_path,
        proof_environment=proof_environment,
        lean_evidence=lean_evidence,
        document_extraction_receipt=document.document_extraction_receipt,
        extraction_receipt=document.extraction_receipt,
    )


def _display_paper_acceptance(
    services: PaperServices,
    output_path: Path,
    claims: list[Claim],
    formalized: int,
    profile: PaperProfile | None,
    coverage_path: Path,
    artifact: PaperArtifact,
) -> None:
    """Display the accepted artifact and every retained source identity."""
    services.console.print(f"\n[bold green]Accepted {output_path}[/]")
    if profile is not None:
        services.console.print(f"  {len(claims)} paper items passed Lean elaboration")
    else:
        services.console.print(f"  {formalized} declarations ready for proving")
    services.console.print(f"  Coverage: {coverage_path}")
    services.console.print(f"  Source:   sha256:{artifact.input_sha256}")
    if artifact.pdf_sha256:
        services.console.print(f"  PDF:      sha256:{artifact.pdf_sha256}")


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
    lean_root = program.parent / cfg.lean_project_path
    runtime = _PaperRuntime(cfg, model, backend, services)
    services.console.print(f"[bold]Analyzing paper: {source}[/]\n")
    try:
        document, paper_artifact, reviewed_profile = _acquire_paper(
            source,
            pages=pages,
            pdf_engine=pdf_engine,
            paddleocr_url=paddleocr_url,
            lean_root=lean_root,
            services=services,
        )
        if extract_only:
            _finish_extraction(services, document, paper_artifact)
            return None, cfg
        claims = _claims_for_verification(document, runtime)
        paper_title = document.title
        formalizer, plan_path, obligations = _plan_paper(
            runtime,
            claims,
            reviewed_profile,
            paper_artifact,
            paper_title=paper_title,
            guidance=guide,
            review_plan=review_plan,
        )
        formalized = _formalize_paper_claims(
            services,
            claims,
            obligations,
            reviewed_profile,
            formalizer,
        )
        output_path, acceptance, content = _accept_paper_source(
            services,
            lean_root,
            output,
            claims,
            reviewed_profile,
            paper_title=paper_title,
        )
        coverage_path = _write_acceptance_coverage(
            lean_root,
            document,
            paper_artifact,
            claims,
            plan_path,
            output_path,
            acceptance,
            content,
        )
        _display_paper_acceptance(
            services,
            output_path,
            claims,
            formalized,
            reviewed_profile,
            coverage_path,
            paper_artifact,
        )
        return (
            PreparedPaper(
                lean_path=output_path,
                source=paper_artifact,
                coverage_path=coverage_path,
                plan_path=plan_path,
                profile_id=reviewed_profile.id if reviewed_profile is not None else "",
                model=formalizer.config.model,
                backend=formalizer.config.backend,
                title=paper_title,
                effort=formalizer.config.effort,
            ),
            cfg,
        )
    finally:
        runtime.close()
