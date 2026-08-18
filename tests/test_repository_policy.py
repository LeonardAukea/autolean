"""Repository policy stays executable at the release boundary."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from autolean.scanner import count_sorries

ROOT = Path(__file__).parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def _tracked_paths(*pathspecs: str) -> list[Path]:
    if not (ROOT / ".git").exists():
        pytest.skip("repository policy requires a Git checkout")
    result = subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "ls-files", "--", *pathspecs],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [ROOT / line for line in result.stdout.splitlines() if (ROOT / line).is_file()]


def test_external_actions_use_full_commit_ids() -> None:
    for workflow in WORKFLOWS.glob("*.yml"):
        for line in workflow.read_text(encoding="utf-8").splitlines():
            match = re.search(r"\buses:\s+([^\s#]+)", line)
            if match is None or match.group(1).startswith("./"):
                continue
            assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", match.group(1)), (
                f"{workflow.name} contains an unpinned action: {match.group(1)}"
            )


def test_release_job_requires_immutability() -> None:
    workflow = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")

    assert "--json isImmutable" in workflow
    assert 'test "$immutable" = true' in workflow


def test_release_job_attests_build_provenance() -> None:
    workflow = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")

    assert "attestations: write" in workflow
    assert "id-token: write" in workflow
    assert "uses: actions/attest-build-provenance@" in workflow
    assert "subject-path: release/*" in workflow
    assert "if: ${{ github.event.repository.visibility == 'public' }}" in workflow


def test_python_matrix_uses_one_pinned_grammar_and_exact_interpreters() -> None:
    workflow = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")

    assert "nix build .#lean-grammar" in workflow
    assert "name: lean-tree-sitter-grammar" in workflow
    assert "needs: grammar" in workflow
    assert "UV_PYTHON: ${{ matrix.python }}" in workflow
    assert "AUTOLEAN_TREE_SITTER_LEAN_LIBRARY:" in workflow
    assert "sys.version_info[:2] == expected" in workflow


def test_documentation_checks_use_the_ci_tools_shell() -> None:
    workflow = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    flake = (ROOT / "flake.nix").read_text(encoding="utf-8")

    assert workflow.count("nix develop .#ci --command") == 3
    assert 'name = "autolean-ci";' in flake
    assert 'name = "autolean";' in flake


def test_default_lean_target_imports_every_shipped_module() -> None:
    root_source = ROOT / "workspace" / "AutoLean.lean"
    imports = set(
        re.findall(
            r"(?m)^import (AutoLean(?:\.[A-Za-z0-9_']+)*)$",
            root_source.read_text(encoding="utf-8"),
        )
    )
    modules = {
        ".".join(path.relative_to(ROOT / "workspace").with_suffix("").parts)
        for path in _tracked_paths("workspace")
        if path.suffix == ".lean"
        and path.parent != ROOT / "workspace"
        and path.is_relative_to(ROOT / "workspace" / "AutoLean")
    }

    assert imports == modules


def test_shipped_lean_modules_are_closed() -> None:
    sources = [
        path
        for path in _tracked_paths("workspace")
        if path.suffix == ".lean" and path.is_relative_to(ROOT / "workspace" / "AutoLean")
    ]

    assert sources
    for source in sources:
        assert count_sorries(source.read_text(encoding="utf-8")) == 0, source


def test_mutable_workspace_outputs_are_untracked() -> None:
    tracked = _tracked_paths(
        "workspace/AutoLean/Generated/**",
        "workspace/AutoLean/Papers/**",
        "workspace/AutoLean/Paper*.lean",
        "workspace/AutoLean/Lib*.lean",
        "workspace/AutoLean/Challenge_*.lean",
        "workspace/AutoLean/UserTheorems.lean",
    )

    assert tracked == []


def test_source_distribution_excludes_mutable_lean_outputs() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    ci = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")

    for project_pattern, archive_pattern in (
        ("/workspace/AutoLean/Generated", "*/workspace/AutoLean/Generated/*"),
        ("/workspace/AutoLean/Papers", "*/workspace/AutoLean/Papers/*"),
        ("/workspace/AutoLean/Paper*.lean", "*/workspace/AutoLean/Paper*.lean"),
        ("/workspace/AutoLean/Challenge_*.lean", "*/workspace/AutoLean/Challenge_*.lean"),
        ("/workspace/AutoLean/Lib*.lean", "*/workspace/AutoLean/Lib*.lean"),
        ("/workspace/AutoLean/UserTheorems.lean", "*/workspace/AutoLean/UserTheorems.lean"),
    ):
        assert f'    "{project_pattern}",' in pyproject
        assert archive_pattern in ci


def test_contribution_interfaces_require_reproducible_evidence() -> None:
    bug = (ROOT / ".github" / "ISSUE_TEMPLATE" / "bug.yml").read_text(encoding="utf-8")
    pull_request = (ROOT / ".github" / "pull_request_template.md").read_text(encoding="utf-8")

    for field in ("behaviour", "expected", "reproduce", "environment", "artifacts", "boundary"):
        assert f"id: {field}" in bug
    for section in ("## Invariant", "## Verification", "## Qualification boundary"):
        assert section in pull_request


def test_release_integrity_is_independently_verifiable() -> None:
    workflow = (WORKFLOWS / "verify-release.yml").read_text(encoding="utf-8")

    assert "workflow_run:" in workflow
    assert "workflows: [CI]" in workflow
    assert "workflow_dispatch:" in workflow
    assert "github.event.workflow_run.head_branch == 'main'" in workflow
    assert "--json isImmutable,targetCommitish" in workflow
    assert "python3 -m autolean.release --revision" in workflow
    assert "github.event.repository.visibility == 'public'" in workflow
    assert 'gh release verify "$RELEASE_TAG"' in workflow
    assert "for attempt in {1..60}" in workflow
    assert "sleep 15" in workflow
    assert 'gh release verify-asset "$RELEASE_TAG"' in workflow
    assert 'gh attestation verify "$asset"' in workflow
    assert "--signer-workflow" in workflow


def test_pypi_publication_requires_a_named_immutable_release() -> None:
    workflow = (WORKFLOWS / "publish-pypi.yml").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "environment: pypi" in workflow
    assert "id-token: write" in workflow
    assert "--json isImmutable,targetCommitish" in workflow
    assert "python3 -m autolean.release --revision" in workflow
    assert "github.event.repository.visibility == 'public'" in workflow
    assert 'gh release verify "$RELEASE_TAG"' in workflow
    assert 'gh attestation verify "$dist"' in workflow
    assert "--signer-workflow" in workflow


def test_the_recorded_demonstration_runs_the_documented_command() -> None:
    """The front page, the tutorial, and the tape name one command."""
    quickstart = 'autolean prove "the Pythagorean theorem" --review-plan'
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    tutorial = (ROOT / "docs" / "tutorials" / "first-proof.md").read_text(encoding="utf-8")
    tape = (ROOT / "docs" / "demos" / "pythagorean.tape").read_text(encoding="utf-8")

    assert quickstart in readme
    assert quickstart in tutorial
    recorded = [line for line in tape.splitlines() if "autolean prove" in line]
    assert len(recorded) == 1
    assert quickstart in recorded[0]


def _formula() -> str:
    return (ROOT / "Formula" / "autolean.rb").read_text(encoding="utf-8")


def test_the_formula_installs_this_distribution() -> None:
    formula = _formula()

    assert 'url "https://github.com/LeonardAukea/autolean/releases/download/' in formula
    assert re.search(r'(?m)^  sha256 "[0-9a-f]{64}"$', formula)
    assert "autolean_proof-" in formula
    assert 'depends_on "elan-init"' in formula


def test_the_formula_carries_every_runtime_dependency() -> None:
    """A dependency added without a resource makes the formula uninstallable."""
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    block = re.search(r"(?ms)^dependencies = \[(.*?)^\]", pyproject)
    assert block is not None
    required = {
        re.split(r"[><=!~\[]", name)[0].strip().lower().replace("_", "-")
        for name in re.findall(r'"([^"]+)"', block.group(1))
    }
    resources = {name.lower() for name in re.findall(r'(?m)^  resource "([^"]+)" do$', _formula())}

    assert required, "no runtime dependencies were parsed"
    assert required <= resources, f"formula has no resource for: {sorted(required - resources)}"
