"""Portable Lean project and companion-paper exports."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

EXPORT_SCHEMA = "autolean.project-export.v1"
_PROJECT_FILES = {"lake-manifest.json", "lakefile.lean", "lakefile.toml", "lean-toolchain"}
_EXCLUDED_PARTS = {
    ".autolean",
    ".git",
    ".lake",
    "build",
    "lake-packages",
    "logs",
    "training_data",
}
_RUNTIME_SOURCE = Path("AutoLean/UserTheorems.lean")
_RUNTIME_SOURCE_DIR = Path("AutoLean/Generated")
_LATEX_ESCAPE = {
    "\\": r"\textbackslash{}",
    "#": r"\#",
    "$": r"\$",
    "%": r"\%",
    "&": r"\&",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "^": r"\textasciicircum{}",
    "~": r"\textasciitilde{}",
}


class ExportError(ValueError):
    """A portable project export cannot be created safely."""


@dataclass(frozen=True)
class ExportResult:
    """Identity and paths for one completed export."""

    path: Path
    manifest_sha256: str
    source_count: int


@dataclass(frozen=True)
class PaperBundle:
    """Exact paper inputs and item-level verification coverage."""

    markdown_path: Path
    coverage_path: Path
    pdf_path: Path | None = None
    plan_path: Path | None = None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _is_runtime_source(relative: Path) -> bool:
    return relative == _RUNTIME_SOURCE or relative.is_relative_to(_RUNTIME_SOURCE_DIR)


def _source_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if any(part in _EXCLUDED_PARTS for part in relative.parts):
            continue
        if path.is_symlink():
            raise ExportError(f"project export does not follow symbolic links: {relative}")
        if (
            path.is_file()
            and (path.suffix == ".lean" or path.name in _PROJECT_FILES)
            and not _is_runtime_source(relative)
        ):
            files.append(path)
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def _lean_module(relative: Path) -> str:
    """Return the import name for one project-relative Lean source."""
    if relative.is_absolute() or relative.suffix != ".lean" or ".." in relative.parts:
        raise ExportError(f"invalid Lean source path: {relative}")
    parts = (*relative.parts[:-1], relative.stem)
    if not all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_']*", part) for part in parts):
        raise ExportError(f"Lean source path has no portable module name: {relative}")
    return ".".join(parts)


def _local_imports(root: Path, source: Path) -> tuple[Path, ...]:
    imports: list[Path] = []
    for match in re.finditer(r"(?m)^[ \t]*import[ \t]+([^\n]+)", source.read_text(encoding="utf-8")):
        for module in re.findall(r"[A-Za-z_][A-Za-z0-9_'.]*", match.group(1)):
            candidate = root.joinpath(*module.split(".")).with_suffix(".lean")
            if candidate.is_file():
                imports.append(candidate)
    return tuple(imports)


def _source_closure(root: Path, roots: tuple[Path, ...]) -> list[Path]:
    """Resolve the project-local import closure of exact session roots."""
    pending = list(roots)
    sources: set[Path] = set()
    while pending:
        source = pending.pop().resolve()
        try:
            relative = source.relative_to(root)
        except ValueError as error:
            raise ExportError(f"session source escapes the Lean project: {source}") from error
        if source.is_symlink():
            raise ExportError(f"project export does not follow symbolic links: {relative}")
        if not source.is_file() or source.suffix != ".lean":
            raise ExportError(f"session source is not a Lean file: {relative}")
        if source in sources:
            continue
        sources.add(source)
        pending.extend(_local_imports(root, source))

    project_files = [root / name for name in _PROJECT_FILES if (root / name).is_file()]
    return sorted(
        [*sources, *project_files],
        key=lambda path: path.relative_to(root).as_posix(),
    )


def _session_roots(
    root: Path,
    session: dict[str, Any] | None,
    coverage: dict[str, Any] | None,
) -> tuple[Path, ...]:
    """Return exact Lean sources named by a session or paper ledger."""
    relative_paths: list[str] = []
    if session is not None and isinstance(session.get("target_file"), str):
        relative_paths.append(session["target_file"])
    if coverage is not None and isinstance(coverage.get("lean_evidence"), dict):
        module = coverage["lean_evidence"].get("module")
        if isinstance(module, str):
            relative_paths.append(module)
    return tuple(root / relative for relative in dict.fromkeys(relative_paths) if relative)


def _write_session_root(project_output: Path, root: Path, source_roots: tuple[Path, ...]) -> None:
    """Create the library root that builds the selected session closure."""
    relative_roots = tuple(source.resolve().relative_to(root) for source in source_roots)
    if Path("AutoLean.lean") in relative_roots:
        return
    imports = "".join(f"import {_lean_module(relative)}\n" for relative in relative_roots)
    (project_output / "AutoLean.lean").write_text(imports, encoding="utf-8")


def _latex_escape(text: str) -> str:
    return "".join(_LATEX_ESCAPE.get(character, character) for character in text)


def _latex_identifier(text: str) -> str:
    """Render a Lean name in a font and shape that can break inside tables."""
    escaped = _latex_escape(text)
    breakable = escaped.replace(".", ".\\allowbreak{}").replace("\\_", "\\_\\allowbreak{}")
    return f"\\texttt{{{breakable}}}"


def _coverage_record(bundle: PaperBundle | None) -> dict[str, Any] | None:
    if bundle is None:
        return None
    for path in (bundle.markdown_path, bundle.coverage_path):
        if not path.is_file():
            raise ExportError(f"paper artifact does not exist: {path}")
    if bundle.pdf_path is not None and not bundle.pdf_path.is_file():
        raise ExportError(f"paper artifact does not exist: {bundle.pdf_path}")
    try:
        record = json.loads(bundle.coverage_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ExportError(f"paper coverage is not valid JSON: {error}") from error
    if not isinstance(record, dict) or record.get("schema") != "autolean.paper-coverage.v2":
        raise ExportError("paper coverage must use autolean.paper-coverage.v2")
    if not isinstance(record.get("claims"), list):
        raise ExportError("paper coverage has no item inventory")
    claims = record["claims"]
    if record.get("total_items") != len(claims):
        raise ExportError("paper coverage item count differs from its inventory")
    elaborated = sum(item.get("status") == "elaborated" for item in claims if isinstance(item, dict))
    if record.get("elaborated_items") != elaborated:
        raise ExportError("paper coverage elaborated count differs from its inventory")
    return record


def paper_bundle_from_artifacts(artifacts: tuple[Path, ...]) -> PaperBundle | None:
    """Resolve one paper bundle from a proof session's exact artifacts."""
    if not artifacts:
        return None
    markdown = [path for path in artifacts if path.suffix.casefold() == ".md"]
    pdf = [path for path in artifacts if path.suffix.casefold() == ".pdf"]
    records: dict[str, Path] = {}
    for path in (candidate for candidate in artifacts if candidate.suffix.casefold() == ".json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ExportError(f"paper session artifact is not valid JSON: {path}") from error
        schema = record.get("schema") if isinstance(record, dict) else None
        if schema in records:
            raise ExportError(f"paper session contains duplicate {schema} artifacts")
        if isinstance(schema, str):
            records[schema] = path
    coverage = records.get("autolean.paper-coverage.v2")
    plan = records.get("autolean.paper-plan.v2")
    if len(markdown) != 1 or coverage is None or plan is None or len(pdf) > 1:
        raise ExportError(
            "paper session must contain one Markdown, one plan, one coverage ledger, and at most one PDF"
        )
    return PaperBundle(markdown[0], coverage, pdf[0] if pdf else None, plan)


def _plan_record(plan_path: Path) -> dict[str, Any]:
    """Read and verify one complete model-response trace."""
    try:
        record = json.loads(plan_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ExportError(f"paper plan is not valid JSON: {error}") from error
    if not isinstance(record, dict) or record.get("schema") != "autolean.paper-plan.v2":
        raise ExportError("paper plan must use autolean.paper-plan.v2")
    responses = _validated_plan_responses(record)
    _validate_plan_trace(record, responses)
    _validate_accepted_plan(record, responses[-1])
    return record


def _validated_plan_responses(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Return response records whose text identities are exact."""
    responses = record.get("responses")
    if not isinstance(responses, list) or not responses:
        raise ExportError("paper plan has no model response trace")
    validated: list[dict[str, Any]] = []
    for response in responses:
        if not isinstance(response, dict) or not isinstance(response.get("response"), str):
            raise ExportError("paper plan response must be an object with exact text")
        actual = hashlib.sha256(response["response"].encode()).hexdigest()
        if response.get("response_sha256") != actual:
            raise ExportError("paper plan response SHA-256 does not match its text")
        validated.append(response)
    return validated


def _validate_plan_trace(record: dict[str, Any], responses: list[dict[str, Any]]) -> None:
    """Check the ordered response trace and accepted-response identity."""
    trace_payload = json.dumps(
        responses,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    trace_sha256 = hashlib.sha256(trace_payload.encode()).hexdigest()
    if record.get("trace_sha256") != trace_sha256:
        raise ExportError("paper plan response trace SHA-256 does not match")
    if record.get("accepted_response_sha256") != responses[-1].get("response_sha256"):
        raise ExportError("paper plan accepted response is not the final response")
    if record.get("accepted_response_model") != responses[-1].get("model"):
        raise ExportError("paper plan accepted model is not the final response model")


def _validate_accepted_plan(record: dict[str, Any], response: dict[str, Any]) -> None:
    """Check that the accepted response parses to the recorded plan."""
    from autolean.strategy import ProofStrategyError, parse_proof_plan

    if response.get("validation_error"):
        raise ExportError("paper plan accepted response failed validation")
    try:
        parsed = parse_proof_plan(response["response"])
    except ProofStrategyError as error:
        raise ExportError(f"paper plan accepted response is invalid: {error}") from error
    if record.get("plan") != parsed.as_dict() or record.get("plan_sha256") != parsed.sha256:
        raise ExportError("paper plan differs from its accepted response")


def _validate_paper_bundle(
    root: Path,
    bundle: PaperBundle,
    coverage: dict[str, Any],
) -> None:
    """Check the links between source, model trace, ledger, and Lean result."""
    if bundle.plan_path is None or not bundle.plan_path.is_file():
        raise ExportError("paper export requires its model plan")
    plan = _plan_record(bundle.plan_path)
    _validate_plan_links(bundle.plan_path, coverage, plan)
    _validate_paper_pdf(bundle.pdf_path, coverage)
    _validate_lean_evidence(root, coverage)


def _validate_plan_links(
    plan_path: Path,
    coverage: dict[str, Any],
    plan: dict[str, Any],
) -> None:
    """Check the coverage ledger's links to its exact model trace."""
    if coverage.get("plan_artifact_sha256") != _sha256(plan_path):
        raise ExportError("paper coverage plan artifact SHA-256 does not match")
    if coverage.get("plan_sha256") != plan.get("plan_sha256"):
        raise ExportError("paper coverage plan SHA-256 does not match")
    if coverage.get("plan_trace_sha256") != plan.get("trace_sha256"):
        raise ExportError("paper coverage response trace SHA-256 does not match")
    for field in ("source_sha256", "text_sha256", "pdf_sha256"):
        if coverage.get(field) != plan.get(field):
            raise ExportError(f"paper coverage {field.replace('_', ' ')} does not match its plan")


def _validate_paper_pdf(pdf_path: Path | None, coverage: dict[str, Any]) -> None:
    """Check the optional source PDF against its ledger identity."""
    if pdf_path is not None:
        if coverage.get("pdf_sha256") != _sha256(pdf_path):
            raise ExportError("paper coverage PDF SHA-256 does not match")
    elif coverage.get("pdf_sha256"):
        raise ExportError("paper coverage identifies a PDF that is absent from the export")


def _validate_lean_evidence(root: Path, coverage: dict[str, Any]) -> None:
    """Check the accepted Lean module and every declared mapping alias."""
    elaborated = int(coverage.get("elaborated_items", 0))
    if elaborated == 0:
        return
    evidence = coverage.get("lean_evidence")
    if not isinstance(evidence, dict):
        raise ExportError("elaborated paper coverage has no Lean evidence")
    if evidence.get("success") is not True or evidence.get("error_count") != 0:
        raise ExportError("paper Lean evidence is not a zero-error result")
    module = evidence.get("module")
    if not isinstance(module, str) or not module:
        raise ExportError("paper Lean evidence has no module path")
    module_path = (root / module).resolve()
    try:
        module_path.relative_to(root)
    except ValueError as error:
        raise ExportError("paper Lean evidence module escapes the project") from error
    if not module_path.is_file() or evidence.get("source_sha256") != _sha256(module_path):
        raise ExportError("paper Lean evidence module SHA-256 does not match")
    declarations = sum(
        len(item.get("evidence_declarations", []))
        for item in coverage["claims"]
        if isinstance(item, dict) and isinstance(item.get("evidence_declarations"), list)
    )
    if evidence.get("declaration_count") != declarations:
        raise ExportError("paper Lean evidence declaration count does not match")
    source = module_path.read_text(encoding="utf-8")
    if len(re.findall(r"(?m)^noncomputable abbrev ", source)) != declarations:
        raise ExportError("paper Lean evidence source does not contain every mapped alias")


def _plan_latex(plan_path: Path | None) -> str:
    if plan_path is None:
        return ""
    if not plan_path.is_file():
        raise ExportError(f"paper plan does not exist: {plan_path}")
    record = _plan_record(plan_path)
    plan = record.get("plan")
    if not isinstance(plan, dict):
        raise ExportError("paper plan has no strategy object")
    lines = [
        "\\section*{Model-reviewed strategy}",
        f"\\noindent Plan SHA-256: \\texttt{{{_latex_escape(str(record.get('plan_sha256', '')))}}}\\par",
        f"\\noindent Response trace SHA-256: "
        f"\\texttt{{{_latex_escape(str(record.get('trace_sha256', '')))}}}\\par",
        f"\\noindent Model: {_latex_escape(str(record.get('model', '')))} via "
        f"{_latex_escape(str(record.get('backend', '')))}\\par",
        f"\\paragraph{{Objective}} {_latex_escape(str(plan.get('objective', '')))}",
    ]
    for field in ("methods", "completion_criteria", "risks", "checkpoints"):
        items = plan.get(field, [])
        if not isinstance(items, list) or not items:
            continue
        heading = field.replace("_", " ").title()
        lines.extend((f"\\paragraph{{{heading}}}", "\\begin{itemize}"))
        lines.extend(f"\\item {_latex_escape(str(item))}" for item in items)
        lines.append("\\end{itemize}")
    return "\n".join(lines) + "\n"


def _coverage_latex(coverage: dict[str, Any] | None) -> str:
    if coverage is None:
        return ""
    profile = coverage.get("profile")
    profile = profile if isinstance(profile, dict) else {}
    rows: list[str] = []
    for item in coverage["claims"]:
        if not isinstance(item, dict):
            raise ExportError("paper coverage item must be an object")
        declarations = item.get("lean_declarations", [])
        if not isinstance(declarations, list):
            raise ExportError("paper coverage declarations must be an array")
        row = " & ".join(
            (
                _latex_escape(str(item.get("label", ""))),
                _latex_escape(str(item.get("scope", ""))),
                _latex_escape(str(item.get("status", ""))),
                ", ".join(_latex_identifier(str(name)) for name in declarations),
            )
        )
        rows.append(row + " \\\\")
    title = _latex_escape(str(profile.get("title", "Reviewed paper")))
    arxiv_id = _latex_escape(str(profile.get("arxiv_id", "not recorded")))
    source_sha = _latex_escape(str(coverage.get("source_sha256", "not recorded")))
    pdf_sha = _latex_escape(str(coverage.get("pdf_sha256", "not recorded")))
    archive_sha = _latex_escape(str(profile.get("source_archive_sha256", "not recorded")))
    total = int(coverage.get("total_items", 0))
    elaborated = int(coverage.get("elaborated_items", 0))
    return (
        "\\section*{Paper provenance}\n"
        f"\\textbf{{{title}}}\\par\n"
        f"\\noindent arXiv revision: \\texttt{{{arxiv_id}}}\\par\n"
        f"\\noindent Source SHA-256: \\texttt{{{source_sha}}}\\par\n"
        f"\\noindent PDF SHA-256: \\texttt{{{pdf_sha}}}\\par\n"
        f"\\noindent Source archive SHA-256: \\texttt{{{archive_sha}}}\\par\n"
        f"\\noindent Coverage: {elaborated} of {total} numbered items elaborated.\\par\n"
        "\\section*{Item coverage}\n"
        "\\small\n"
        "\\begin{longtable}{"
        ">{\\raggedright\\arraybackslash}p{0.14\\linewidth}"
        ">{\\raggedright\\arraybackslash}p{0.11\\linewidth}"
        ">{\\raggedright\\arraybackslash}p{0.10\\linewidth}"
        ">{\\raggedright\\arraybackslash}p{0.54\\linewidth}}\n"
        "\\textbf{Item} & \\textbf{Scope} & \\textbf{Status} & "
        "\\textbf{Lean declarations} \\\\ \\hline\n" + "\n".join(rows) + "\n\\end{longtable}\n\\normalsize\n"
    )


def _paper_source(
    title: str,
    lean_files: list[str],
    environment_sha256: str,
    coverage: dict[str, Any] | None = None,
    plan_path: Path | None = None,
) -> str:
    sections = []
    for path in lean_files:
        sections.append(
            "\\subsection*{\\texttt{\\detokenize{" + path + "}}}\n"
            "\\VerbatimInput[fontsize=\\scriptsize]{../project/" + path + "}\n"
        )
    environment = environment_sha256 or "not recorded"
    return (
        "\\documentclass[11pt]{article}\n"
        "\\usepackage{fontspec}\n"
        "\\usepackage{ucharclasses}\n"
        "\\setmonofont{DejaVuSansMono.ttf}[\n"
        "  BoldFont=DejaVuSansMono-Bold.ttf,\n"
        "  ItalicFont=DejaVuSansMono-Oblique.ttf,\n"
        "  Scale=MatchLowercase\n"
        "]\n"
        "\\newfontfamily\\mathfallbackfont{DejaVuSans.ttf}\n"
        "\\setTransitionsForMathematics{\\mathfallbackfont}{\\ttfamily}\n"
        "\\usepackage{amsmath,amsthm}\n"
        "\\usepackage{array,longtable}\n"
        "\\usepackage[margin=1in]{geometry}\n"
        "\\usepackage{fancyvrb}\n"
        "\\usepackage[hidelinks]{hyperref}\n"
        f"\\title{{{_latex_escape(title)}}}\n"
        "\\author{AutoLean proof artifact}\n"
        "\\date{}\n"
        "\\begin{document}\n"
        "\\maketitle\n"
        "\\section*{Verification contract}\n"
        "The mathematical declarations in this artifact are checked by the pinned "
        "Lean toolchain. The accompanying manifest binds every source file to its "
        "SHA-256 digest.\\par\n"
        f"\\noindent Environment SHA-256: \\texttt{{{environment}}}\n"
        + _coverage_latex(coverage)
        + _plan_latex(plan_path)
        + "\\section*{Lean source}\n"
        + "\n".join(sections)
        + "\\end{document}\n"
    )


def _readme(title: str, environment_sha256: str) -> str:
    environment = environment_sha256 or "not recorded"
    return (
        f"# {title}\n\n"
        "This directory is a standalone Lean project exported by AutoLean.\n\n"
        "Build the formal artifact with:\n\n"
        "```console\n"
        "cd project\n"
        "lake build\n"
        "```\n\n"
        "Build the companion paper with a TeX distribution that provides "
        "`fancyvrb`:\n\n"
        "```console\n"
        "cd paper\n"
        "latexmk -xelatex main.tex\n"
        "```\n\n"
        f"Proof environment SHA-256: `{environment}`.\n"
        "Paper inputs and item-level coverage are under `source/` when the "
        "export comes from a paper session. See `manifest.json` for exact "
        "source identities.\n"
    )


def _validated_destination(project_root: Path, output: Path) -> tuple[Path, Path]:
    """Resolve an export destination outside a complete Lean project."""
    root = project_root.resolve()
    destination = output.resolve()
    try:
        destination.relative_to(root)
    except ValueError:
        pass
    else:
        raise ExportError("export destination must be outside the Lean project")
    if destination.exists():
        raise ExportError(f"export destination already exists: {destination}")
    if not (root / "lean-toolchain").is_file():
        raise ExportError(f"Lean project has no lean-toolchain: {root}")
    if not ((root / "lakefile.lean").is_file() or (root / "lakefile.toml").is_file()):
        raise ExportError(f"Lean project has no Lake configuration: {root}")
    return root, destination


def _copy_project(
    root: Path,
    project_output: Path,
    session: dict[str, Any] | None,
    coverage: dict[str, Any] | None,
) -> list[str]:
    """Copy the exact selected source closure into a standalone project."""
    source_roots = _session_roots(root, session, coverage)
    sources = _source_closure(root, source_roots) if source_roots else _source_files(root)
    for source in sources:
        relative = source.relative_to(root)
        target = project_output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    if source_roots:
        _write_session_root(project_output, root, source_roots)
    return sorted(
        path.relative_to(project_output).as_posix()
        for path in project_output.rglob("*.lean")
        if path.name not in _PROJECT_FILES
    )


def _copy_paper_sources(staging: Path, bundle: PaperBundle) -> None:
    """Copy the provenance-bound source artifacts for one paper session."""
    source_output = staging / "source"
    source_output.mkdir(parents=True)
    shutil.copyfile(bundle.markdown_path, source_output / "paper.md")
    shutil.copyfile(bundle.coverage_path, source_output / "coverage.json")
    if bundle.pdf_path is not None:
        shutil.copyfile(bundle.pdf_path, source_output / "paper.pdf")
    if bundle.plan_path is not None:
        shutil.copyfile(bundle.plan_path, source_output / "plan.json")


def _write_export_documents(
    staging: Path,
    *,
    title: str,
    lean_files: list[str],
    environment_sha256: str,
    coverage: dict[str, Any] | None,
    session: dict[str, Any] | None,
    paper_bundle: PaperBundle | None,
) -> None:
    """Write the companion paper, instructions, and optional session record."""
    paper = staging / "paper" / "main.tex"
    paper.parent.mkdir(parents=True)
    paper.write_text(
        _paper_source(
            title,
            lean_files,
            environment_sha256,
            coverage,
            paper_bundle.plan_path if paper_bundle is not None else None,
        ),
        encoding="utf-8",
    )
    if paper_bundle is not None:
        _copy_paper_sources(staging, paper_bundle)
    (staging / "README.md").write_text(
        _readme(title, environment_sha256),
        encoding="utf-8",
    )
    if session is not None:
        (staging / "session.json").write_text(
            json.dumps(session, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def _write_export_manifest(
    staging: Path,
    *,
    title: str,
    environment_sha256: str,
    coverage: dict[str, Any] | None,
) -> str:
    """Write and return the digest of the complete artifact manifest."""
    artifact_files = sorted(
        (path for path in staging.rglob("*") if path.is_file() and path.name != "manifest.json"),
        key=lambda path: path.relative_to(staging).as_posix(),
    )
    manifest = {
        "environment_sha256": environment_sha256,
        "files": [
            {
                "path": path.relative_to(staging).as_posix(),
                "sha256": _sha256(path),
                "size": path.stat().st_size,
            }
            for path in artifact_files
        ],
        "schema": EXPORT_SCHEMA,
        "title": title,
        "paper_profile": coverage.get("profile") if coverage is not None else None,
    }
    manifest_path = staging / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return _sha256(manifest_path)


def export_project(
    project_root: Path,
    output: Path,
    *,
    title: str,
    environment_sha256: str = "",
    session: dict[str, Any] | None = None,
    paper_bundle: PaperBundle | None = None,
) -> ExportResult:
    """Create one atomic, source-only Lean and LaTeX artifact."""
    root, destination = _validated_destination(project_root, output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    try:
        coverage = _coverage_record(paper_bundle)
        if paper_bundle is not None and coverage is not None:
            _validate_paper_bundle(root, paper_bundle, coverage)
        lean_files = _copy_project(root, staging / "project", session, coverage)
        _write_export_documents(
            staging,
            title=title,
            lean_files=lean_files,
            environment_sha256=environment_sha256,
            coverage=coverage,
            session=session,
            paper_bundle=paper_bundle,
        )
        manifest_sha256 = _write_export_manifest(
            staging,
            title=title,
            environment_sha256=environment_sha256,
            coverage=coverage,
        )
        os.replace(staging, destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return ExportResult(destination, manifest_sha256, len(lean_files))
