"""Reviewed paper mappings, plans, coverage, and Lean evidence."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autolean.generated_code import (
    safe_lean_comment_text,
    validate_generated_closed_declarations,
    validate_generated_declarations,
)
from autolean.paper import (
    Claim,
    ClaimDisposition,
    PaperArtifact,
    _sha256_file,
    _write_exact_text,
    normalize_claim_kind,
)
from autolean.paper_profiles import (
    PaperProfile,
    PaperProfileError,
    match_paper_profile,
)

if TYPE_CHECKING:
    from autolean.strategy import PlanAttempt, ProofPlan


def analyze_paper_structure(claims: list[Claim]) -> dict[str, Any]:
    """Summarize paper items by kind, workflow, and proof coverage."""
    by_kind: dict[str, int] = {}
    by_disposition = {item.value: 0 for item in ClaimDisposition}
    for claim in claims:
        kind = normalize_claim_kind(claim.kind)
        by_kind[kind] = by_kind.get(kind, 0) + 1
        by_disposition[claim.disposition.value] += 1
    return {
        "total_claims": len(claims),
        "by_kind": by_kind,
        "by_disposition": by_disposition,
        "with_proof": sum(bool(claim.proof_sketch) for claim in claims),
        "provable": [claim for claim in claims if claim.disposition is ClaimDisposition.PROVE],
        "proof_obligations": [claim for claim in claims if claim.disposition is ClaimDisposition.PROVE],
        "definitions": [claim for claim in claims if claim.disposition is ClaimDisposition.DEFINE],
        "open_boundaries": [claim for claim in claims if claim.disposition is ClaimDisposition.OPEN],
        "context": [claim for claim in claims if claim.disposition is ClaimDisposition.CONTEXT],
        "remarks": [claim for claim in claims if normalize_claim_kind(claim.kind) == "remark"],
    }


def write_paper_plan(
    artifact: PaperArtifact,
    plan: ProofPlan,
    *,
    model: str,
    backend: str,
    responses: tuple[PlanAttempt, ...],
) -> Path:
    """Write the accepted plan and every exact provider response."""
    from autolean.strategy import parse_proof_plan

    if not responses:
        raise ValueError("paper planning requires at least one model response")
    accepted = responses[-1]
    if accepted.validation_error:
        raise ValueError("the accepted paper plan response failed validation")
    if parse_proof_plan(accepted.response).sha256 != plan.sha256:
        raise ValueError("the accepted model response differs from the paper plan")
    response_records = [response.as_dict() for response in responses]
    trace_payload = json.dumps(
        response_records,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    trace_sha256 = hashlib.sha256(trace_payload.encode()).hexdigest()
    record = {
        "accepted_response_model": accepted.model,
        "accepted_response_sha256": accepted.response_sha256,
        "backend": backend,
        "model": model,
        "pdf_sha256": artifact.pdf_sha256,
        "plan": plan.as_dict(),
        "plan_sha256": plan.sha256,
        "responses": response_records,
        "schema": "autolean.paper-plan.v2",
        "source_sha256": artifact.input_sha256,
        "text_sha256": artifact.text_sha256,
        "trace_sha256": trace_sha256,
    }
    payload = json.dumps(record, indent=2, sort_keys=True) + "\n"
    artifact_sha256 = hashlib.sha256(payload.encode()).hexdigest()
    path = artifact.markdown_path.with_name(f"{artifact.markdown_path.stem}_plan_{artifact_sha256[:12]}.json")
    _write_exact_text(path, payload, label="paper plan")
    return path


def _paper_profile(artifact: PaperArtifact, claims: list[Claim]) -> PaperProfile | None:
    profile_ids = sorted({claim.profile_id for claim in claims if claim.profile_id})
    if len(profile_ids) > 1:
        raise ValueError("paper coverage cannot combine reviewed profiles")
    return match_paper_profile(artifact.pdf_sha256)


def _plan_identity(plan_path: Path | None) -> tuple[str, str]:
    if plan_path is None:
        return "", ""
    record = json.loads(plan_path.read_text(encoding="utf-8"))
    if not isinstance(record, dict) or record.get("schema") != "autolean.paper-plan.v2":
        raise ValueError("paper plan must use autolean.paper-plan.v2")
    return str(record.get("plan_sha256", "")), str(record.get("trace_sha256", ""))


def _validate_lean_evidence(claims: list[Claim], evidence: dict[str, Any] | None) -> int:
    elaborated_items = sum(claim.elaborated for claim in claims)
    if not elaborated_items:
        return 0
    if evidence is None:
        raise ValueError("elaborated paper coverage requires Lean evidence")
    declaration_count = sum(len(claim.evidence_names) for claim in claims)
    if evidence.get("declaration_count") != declaration_count:
        raise ValueError("Lean evidence declaration count differs from paper mappings")
    if evidence.get("success") is not True or evidence.get("error_count") != 0:
        raise ValueError("elaborated paper coverage requires a zero-error Lean result")
    return elaborated_items


def _profile_record(profile: PaperProfile | None) -> dict[str, object] | None:
    if profile is None:
        return None
    return {
        "arxiv_id": profile.arxiv_id,
        "authors": list(profile.authors),
        "id": profile.id,
        "source_archive_sha256": profile.source_archive_sha256,
        "title": profile.title,
    }


def _coverage_claim(claim: Claim) -> dict[str, object]:
    return {
        "disposition": claim.disposition.value,
        "elaborated": claim.elaborated,
        "evidence_declarations": list(claim.evidence_names),
        "evidence_source_present": bool(claim.lean_code),
        "kind": normalize_claim_kind(claim.kind),
        "label": claim.label,
        "lean_declarations": list(claim.lean_declarations),
        "lean_name": claim.lean_name,
        "profile_id": claim.profile_id,
        "scope": claim.profile_scope,
        "source_ref": claim.input_ref,
        "source_sha256": claim.input_sha256,
        "statement": claim.statement,
        "statement_sha256": hashlib.sha256(claim.statement.encode()).hexdigest(),
        "status": _claim_coverage_status(claim),
    }


def write_paper_coverage(
    artifact: PaperArtifact,
    claims: list[Claim],
    *,
    plan_path: Path | None = None,
    proof_environment: dict[str, Any] | None = None,
    lean_evidence: dict[str, Any] | None = None,
) -> Path:
    """Write a content-addressed ledger for every extracted paper item."""
    analysis = analyze_paper_structure(claims)
    plan_sha256, plan_trace_sha256 = _plan_identity(plan_path)
    profile = _paper_profile(artifact, claims)
    elaborated_items = _validate_lean_evidence(claims, lean_evidence)
    ledger = {
        "by_disposition": analysis["by_disposition"],
        "claims": [_coverage_claim(claim) for claim in claims],
        "pdf_sha256": artifact.pdf_sha256,
        "lean_evidence": lean_evidence,
        "profile": _profile_record(profile),
        "schema": "autolean.paper-coverage.v2",
        "plan_artifact_sha256": _sha256_file(plan_path) if plan_path is not None else "",
        "plan_sha256": plan_sha256,
        "plan_trace_sha256": plan_trace_sha256,
        "proof_environment": proof_environment,
        "source_sha256": artifact.input_sha256,
        "text_sha256": artifact.text_sha256,
        "total_items": len(claims),
        "elaborated_items": elaborated_items,
    }
    payload = json.dumps(ledger, indent=2, sort_keys=True) + "\n"
    digest = hashlib.sha256(payload.encode()).hexdigest()
    path = artifact.markdown_path.with_name(f"{artifact.markdown_path.stem}_coverage_{digest[:12]}.json")
    _write_exact_text(path, payload, label="paper coverage ledger")
    return path


def _claim_coverage_status(claim: Claim) -> str:
    """Return the strongest completed workflow state for one paper item."""
    if claim.elaborated:
        return "elaborated"
    if claim.lean_declarations:
        return "mapped"
    if claim.lean_code:
        return "formalized"
    if claim.disposition is ClaimDisposition.OPEN:
        return "open"
    if claim.disposition is ClaimDisposition.CONTEXT:
        return "context"
    return "extracted"


_PROFILE_ALIAS_PART = re.compile(r"[^A-Za-z0-9]+")


def _profile_alias(profile: PaperProfile, label: str, index: int) -> str:
    profile_part = _PROFILE_ALIAS_PART.sub("_", profile.id).strip("_")
    label_part = _PROFILE_ALIAS_PART.sub("_", label).strip("_")
    return f"autoleanPaper_{profile_part}_{label_part}_{index}"


def bind_reviewed_paper(
    claims: list[Claim],
    artifact: PaperArtifact,
) -> PaperProfile | None:
    """Bind an exact reviewed PDF to its complete numbered Lean inventory."""
    profile = match_paper_profile(artifact.pdf_sha256)
    if profile is None:
        return None

    claims_by_label = {claim.label: claim for claim in claims}
    if len(claims_by_label) != len(claims):
        raise PaperProfileError(f"paper extraction contains duplicate labels for {profile.id}")
    expected = set(profile.item_by_label)
    actual = set(claims_by_label)
    if actual != expected:
        missing = ", ".join(sorted(expected - actual)) or "none"
        unexpected = ", ".join(sorted(actual - expected)) or "none"
        raise PaperProfileError(
            f"reviewed paper inventory differs for {profile.id}; missing: {missing}; unexpected: {unexpected}"
        )

    for item in profile.items:
        claim = claims_by_label[item.label]
        aliases = tuple(
            _profile_alias(profile, item.label, index) for index in range(1, len(item.declarations) + 1)
        )
        declarations = [
            f"noncomputable abbrev {alias} := @{declaration.name}"
            for alias, declaration in zip(aliases, item.declarations, strict=True)
        ]
        claim.lean_name = aliases[0]
        claim.lean_code = validate_generated_closed_declarations("\n".join(declarations))
        claim.lean_declarations = tuple(declaration.name for declaration in item.declarations)
        claim.evidence_names = aliases
        claim.profile_id = profile.id
        claim.profile_scope = item.scope.value
        claim.elaborated = False
    return profile


def mark_reviewed_paper_elaborated(claims: list[Claim], profile: PaperProfile) -> None:
    """Mark every item after its complete evidence source elaborates."""
    if any(claim.profile_id != profile.id or not claim.lean_code for claim in claims):
        raise PaperProfileError(f"paper evidence is incomplete for {profile.id}")
    for claim in claims:
        claim.elaborated = True


def render_verification_source(
    claims: list[Claim],
    paper_title: str = "Unknown Paper",
    imports: tuple[str, ...] = ("Mathlib",),
) -> str:
    """Render complete Lean source for formalized paper claims."""
    if not imports or any(not module.strip() for module in imports):
        raise ValueError("paper evidence requires at least one valid Lean import")
    safe_title = safe_lean_comment_text(paper_title)
    parts = [
        *(f"import {module}" for module in imports),
        "",
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
    parts.extend(["-/", ""])

    for claim in claims:
        label = safe_lean_comment_text(claim.label)
        statement = safe_lean_comment_text(claim.statement)
        parts.append(f"-- [{label}] ({claim.disposition.value}): {statement[:120]}")
        if claim.proof_sketch:
            sketch = safe_lean_comment_text(claim.proof_sketch)
            parts.append(f"-- Proof sketch: {sketch[:100]}...")

        if claim.disposition in {ClaimDisposition.CONTEXT, ClaimDisposition.OPEN}:
            parts.append("-- Recorded as a source boundary; no Lean declaration is generated.")
        elif claim.lean_code:
            validated_code = validate_generated_declarations(claim.lean_code)
            code_lines = [
                line
                for line in validated_code.split("\n")
                if not line.strip().startswith(("import ", "-- import"))
            ]
            parts.append("\n".join(code_lines))
        else:
            lean_name = safe_lean_comment_text(claim.lean_name)
            parts.append(f"-- Formalization pending: {statement[:80]}")
            parts.append(f"-- theorem {lean_name} : sorry := sorry")
        parts.append("")

    return "\n".join(parts)
