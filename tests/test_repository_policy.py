"""Repository policy stays executable at the release boundary."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


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
