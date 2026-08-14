"""Opt-in host tests for the generated-code operating-system sandbox."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from autolean.lean_interface import CORE_LOGICAL_AXIOMS, LeanProject

pytestmark = pytest.mark.skipif(
    os.environ.get("AUTOLEAN_RUN_SANDBOX_E2E") != "1",
    reason="set AUTOLEAN_RUN_SANDBOX_E2E=1 for host sandbox checks",
)

_MATHLIB_E2E_TIMEOUT_SECONDS = 240


@pytest.fixture()
def project_and_source() -> tuple[LeanProject, Path]:
    configured = os.environ.get("AUTOLEAN_SANDBOX_PROJECT")
    root = Path(configured) if configured is not None else Path(__file__).resolve().parents[1] / "workspace"
    configured_source = os.environ.get("AUTOLEAN_SANDBOX_SOURCE")
    if configured_source is not None:
        source = Path(configured_source)
    elif configured is not None:
        source = root / "AutoLean" / "Target.lean"
    else:
        source = root / "AutoLean" / "Trivial.lean"
    return LeanProject(root), source


def test_sandbox_removes_parent_credentials(
    project_and_source: tuple[LeanProject, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, source = project_and_source
    original = source.read_bytes()
    monkeypatch.setenv("AUTOLEAN_SANDBOX_SENTINEL", "must-not-cross")
    candidate = """\
import Lean

example : True := by
  run_tac do
    if (← IO.getEnv "AUTOLEAN_SANDBOX_SENTINEL").isSome then
      throwError "parent environment crossed the sandbox"
  exact True.intro
"""
    assert project.validate_candidate(source, candidate).success
    assert source.read_bytes() == original


def test_sandbox_denies_writes_outside_scratch(
    project_and_source: tuple[LeanProject, Path],
) -> None:
    project, source = project_and_source
    original = source.read_bytes()
    outside = Path(tempfile.gettempdir()) / f"autolean-sandbox-escape-{os.getpid()}"
    assert not outside.exists()
    candidate = f"""\
import Lean

example : True := by
  run_tac do
    IO.FS.writeFile "{outside}" "escaped"
  exact True.intro
"""
    result = project.validate_candidate(source, candidate)
    assert result.success is False
    assert not outside.exists()
    assert source.read_bytes() == original


def test_sandbox_denies_reads_outside_allowlist(
    project_and_source: tuple[LeanProject, Path],
) -> None:
    project, source = project_and_source
    original = source.read_bytes()
    with tempfile.NamedTemporaryFile("w", encoding="utf-8") as sentinel:
        sentinel.write("must-not-cross")
        sentinel.flush()
        candidate = f'''\
import Lean

example : True := by
  run_tac do
    let value? ← try
      pure (some (← IO.FS.readFile "{sentinel.name}"))
    catch _ =>
      pure (none : Option String)
    if value?.isSome then
      throwError "host file crossed the sandbox"
  exact True.intro
'''
        assert project.validate_candidate(source, candidate).success
    assert source.read_bytes() == original


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        del format, args


def test_sandbox_denies_loopback_network(
    project_and_source: tuple[LeanProject, Path],
) -> None:
    project, source = project_and_source
    original = source.read_bytes()
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        curl = shutil.which("curl")
        assert curl is not None
        url = f"http://127.0.0.1:{server.server_port}/"
        candidate = f"""\
import Lean

example : True := by
  run_tac do
    let output ← IO.Process.output {{
      cmd := "{curl}"
      args := #["--silent", "--fail", "--max-time", "2", "{url}"]
    }}
    if output.exitCode == 0 then
      throwError "network crossed the sandbox"
  exact True.intro
"""
        assert project.validate_candidate(source, candidate).success
        assert source.read_bytes() == original
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_axiom_audit_accepts_kernel_checked_declaration(
    project_and_source: tuple[LeanProject, Path],
) -> None:
    project, source = project_and_source
    candidate = """\
import Lean

theorem AutoLeanSandboxClean : True := by
  trivial
"""

    result = project.validate_candidate(
        source,
        candidate,
        declaration="AutoLeanSandboxClean",
        declaration_line=3,
    )

    assert result.success
    assert result.axioms == ()


def test_axiom_audit_rejects_injected_axiom(
    project_and_source: tuple[LeanProject, Path],
) -> None:
    project, source = project_and_source
    candidate = """\
import Lean

axiom AutoLeanInjected : False

theorem AutoLeanSandboxUnsound : False := AutoLeanInjected
"""

    result = project.validate_candidate(
        source,
        candidate,
        declaration="AutoLeanSandboxUnsound",
        declaration_line=5,
    )

    assert not result.success
    assert result.axioms == ("AutoLeanInjected",)
    assert "disallowed axioms" in result.errors[0].message


def test_axiom_audit_binds_name_to_the_exact_source_range(
    project_and_source: tuple[LeanProject, Path],
) -> None:
    project, source = project_and_source
    candidate = """\
import Lean

theorem preceding : True := by
  trivial

@[simp] theorem attributed : True := by
  trivial
"""

    wrong = project.validate_candidate(
        source,
        candidate,
        declaration="preceding",
        declaration_line=7,
    )
    exact = project.validate_candidate(
        source,
        candidate,
        declaration="attributed",
        declaration_line=7,
    )

    assert not wrong.success
    assert "outside preceding" in f"{wrong.stdout}\n{wrong.stderr}"
    assert exact.success
    assert exact.axioms == ()


def test_audit_refuses_a_candidate_that_weakens_the_statement(
    project_and_source: tuple[LeanProject, Path],
) -> None:
    """The name and line are satisfied by a theorem proving something else."""
    project, source = project_and_source
    honest = """\
import Lean

theorem bounded (a b : Nat) (h : b <= a) :
    a + b <= 2 * a := by
  omega
"""
    weakened = honest.replace("    a + b <= 2 * a := by\n  omega", "    True := by\n  trivial")

    baseline = project.validate_candidate(source, honest, declaration="bounded", declaration_line=3)
    assert baseline.success
    assert baseline.statement_sha256

    unpinned = project.validate_candidate(source, weakened, declaration="bounded", declaration_line=3)
    pinned = project.validate_candidate(
        source,
        weakened,
        declaration="bounded",
        declaration_line=3,
        expected_statement=baseline.statement_sha256,
    )
    rewritten_proof = project.validate_candidate(
        source,
        honest.replace("  omega", "  have hb := h\n  omega"),
        declaration="bounded",
        declaration_line=3,
        expected_statement=baseline.statement_sha256,
    )

    assert unpinned.success, "the name and line alone accept a weaker theorem"
    assert not pinned.success
    assert "no longer states what it was asked to prove" in pinned.errors[0].message
    assert rewritten_proof.success, "a different proof of the same statement must still pass"


def test_pythagorean_formalization_and_proof_reach_isolated_lean(
    project_and_source: tuple[LeanProject, Path],
) -> None:
    project, source = project_and_source
    if not (project.root / ".lake" / "packages" / "mathlib").exists():
        pytest.skip("the sandbox fixture has no Mathlib closure")

    from autolean.llm import LLMResponse
    from autolean.strategy import ProofPlan
    from autolean.theorem import formalize_theorem

    scaffold = """\
open RealInnerProductSpace

theorem pythagorean_theorem
    {V : Type*} [NormedAddCommGroup V] [InnerProductSpace ℝ V]
    (x y : V) (h : ⟪x, y⟫ = 0) :
    ‖x + y‖ ^ 2 = ‖x‖ ^ 2 + ‖y‖ ^ 2 := by
  sorry
"""
    plan = ProofPlan(
        objective="Prove norm additivity for orthogonal vectors.",
        formalization=("Use the real inner product and explicit scalar notation.",),
        methods=("Apply Mathlib's inner-product Pythagorean identity.",),
        checkpoints=("Compile the exact theorem scaffold.",),
    )

    theorem = formalize_theorem(
        "the pythagorean theorem",
        plan,
        lambda _system, _user: LLMResponse(text=scaffold, model="fixture"),
        project,
        max_repairs=0,
        timeout=_MATHLIB_E2E_TIMEOUT_SECONDS,
    )

    proof_source = theorem.source.replace(
        "  sorry\n",
        ("  simpa [pow_two] using\n    norm_add_sq_eq_norm_sq_add_norm_sq_of_inner_eq_zero x y h\n"),
    )
    result = project.validate_candidate(
        source,
        proof_source,
        declaration=theorem.declaration_name,
        declaration_line=theorem.declaration_line,
        timeout=_MATHLIB_E2E_TIMEOUT_SECONDS,
    )

    assert theorem.declaration_name == "pythagorean_theorem"
    assert "riemann_hypothesis" not in theorem.source
    assert result.success, result.stderr or result.errors
    assert set(result.axioms or ()) <= CORE_LOGICAL_AXIOMS
    assert "sorryAx" not in (result.axioms or ())


def test_runnable_challenge_sources_are_well_formed(
    project_and_source: tuple[LeanProject, Path],
) -> None:
    project, source = project_and_source
    if not (project.root / ".lake" / "packages" / "mathlib").exists():
        pytest.skip("the sandbox fixture has no Mathlib closure")

    from autolean.challenges import OPEN_PROBLEMS, render_challenge_source

    for problem in OPEN_PROBLEMS:
        if problem.formalization_status != "formalized":
            continue
        result = project.validate_candidate(source, render_challenge_source(problem), timeout=120)
        assert result.success, f"{problem.id}: {result.stderr or result.errors}"


def test_reviewed_ionescu_tulcea_inventory_reaches_isolated_lean(
    project_and_source: tuple[LeanProject, Path],
) -> None:
    project, source = project_and_source
    if not (project.root / ".lake" / "packages" / "mathlib").exists():
        pytest.skip("the sandbox fixture has no Mathlib closure")

    from autolean.paper import Claim, PaperArtifact
    from autolean.paper_evidence import bind_reviewed_paper, render_verification_source
    from autolean.paper_profiles import IONESCU_TULCEA_V5

    claims = [
        Claim(
            item.label,
            f"Extracted statement for {item.label}.",
            kind=item.label.split()[0],
            input_ref="https://arxiv.org/pdf/2506.18616v5.pdf",
            input_sha256=IONESCU_TULCEA_V5.pdf_sha256,
        )
        for item in IONESCU_TULCEA_V5.items
    ]
    artifact = PaperArtifact(
        markdown_path=Path("paper.md"),
        pdf_path=None,
        input_sha256=IONESCU_TULCEA_V5.pdf_sha256,
        text_sha256="0" * 64,
        pdf_sha256=IONESCU_TULCEA_V5.pdf_sha256,
    )
    profile = bind_reviewed_paper(claims, artifact)
    assert profile is IONESCU_TULCEA_V5
    evidence = render_verification_source(claims, profile.title, imports=profile.imports)

    result = project.validate_candidate(source, evidence, timeout=300)

    assert result.success, result.stderr or result.errors
    assert len(claims) == 25
    assert sum(len(claim.evidence_names) for claim in claims) == 33
    assert re.search(r"(?m)^[ \t]*sorry\b", evidence) is None


def test_the_sandbox_rejects_an_auto_bound_identifier(tmp_path: Path) -> None:
    """A statement the project would reject must not pass validation.

    Lake applies the project's `autoImplicit := false`; the sandbox invokes
    Lean directly, so without the same option an unbound identifier becomes a
    fresh implicit argument and the candidate means something else.
    """
    if not os.environ.get("AUTOLEAN_RUN_SANDBOX_E2E"):
        pytest.skip("set AUTOLEAN_RUN_SANDBOX_E2E=1 to run host containment tests")

    (tmp_path / "lakefile.lean").write_text("-- lakefile\n", encoding="utf-8")
    project = LeanProject(tmp_path)
    source = tmp_path / "T.lean"
    source.write_text("theorem placeholder : True := by\n  sorry\n", encoding="utf-8")

    result = project.validate_candidate(
        source,
        "theorem symm_typo (n : Nat) (h : n = m) : m = n := h.symm\n",
    )

    assert not result.success
    assert any("Unknown identifier" in d.message for d in result.errors)
