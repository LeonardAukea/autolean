"""Controls for budget, identity, and feedback in paired experiments."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autolean.lean_interface import BuildResult, Diagnostic, LeanProject
from autolean.llm import BaseBackend, LLMConfig, LLMError, LLMResponse
from autolean.program import ProgramConfig
from scripts.compare_provers import RecordedBackend, _vanilla, load_cases, paired_summary


class Replies(BaseBackend):
    def __init__(self, responses: list[LLMResponse | LLMError]) -> None:
        super().__init__(LLMConfig(model="fixed-model", temperature=0.2, max_output_tokens=16))
        self.responses = iter(responses)
        self.temperatures: list[float | None] = []

    def generate(self, system: str, user: str, *, temperature=None, stop=None) -> LLMResponse:
        self.temperatures.append(temperature)
        response = next(self.responses)
        if isinstance(response, LLMError):
            raise response
        return response


def test_budget_accounts_for_every_request_and_preserves_failures(tmp_path: Path) -> None:
    inner = Replies([LLMError("offline"), LLMResponse("rfl", "fixed-model", 5, 3)])
    backend = RecordedBackend(inner, 2, tmp_path / "calls.jsonl")
    with pytest.raises(LLMError, match="offline"):
        backend.generate("strategy", "request", temperature=0.9)
    assert backend.generate("proof", "request", temperature=0.8).text == "rfl"
    with pytest.raises(LLMError, match="budget"):
        backend.generate("proof", "request")
    assert backend.exhausted
    assert inner.temperatures == [0.2, 0.2]
    rows = [json.loads(line) for line in backend.path.read_text().splitlines()]
    assert len(rows) == 2
    assert rows[0]["error"] == "offline"
    assert rows[1]["text"] == "rfl"
    assert rows[1]["input_tokens"] == 5
    assert rows[1]["output_tokens"] == 3


def test_provider_output_ceiling_violation_is_recorded(tmp_path: Path) -> None:
    backend = RecordedBackend(
        Replies([LLMResponse("rfl", "fixed-model", 5, 17)]), 1, tmp_path / "calls.jsonl"
    )
    with pytest.raises(LLMError, match="ceiling"):
        backend.generate("proof", "request")
    row = json.loads(backend.path.read_text())
    assert row["output_tokens"] == 17
    assert "ceiling" in row["error"]


def _arm(*, accepted: bool, model: str = "fixed-model") -> dict[str, object]:
    return {
        "accepted": accepted,
        "status": "completed",
        "source_sha256": "s",
        "project_source_sha256": "p",
        "environment_sha256": "e",
        "calls": [{"model": model}],
    }


@pytest.mark.parametrize(
    ("auto", "vanilla", "verdict"),
    [(True, True, "both"), (True, False, "autolean"), (False, True, "vanilla"), (False, False, "neither")],
)
def test_paired_summary_preserves_losses_and_ties(auto: bool, vanilla: bool, verdict: str) -> None:
    pair = {"arms": {"autolean": _arm(accepted=auto), "vanilla": _arm(accepted=vanilla)}}
    assert paired_summary([pair])[verdict] == 1
    assert pair["comparable"] is True


@pytest.mark.parametrize(
    "difference", ["source_sha256", "project_source_sha256", "environment_sha256", "model"]
)
def test_changed_identity_invalidates_the_pair(difference: str) -> None:
    auto, vanilla = _arm(accepted=True), _arm(accepted=False)
    if difference == "model":
        vanilla["calls"] = [{"model": "other-model"}]
    else:
        vanilla[difference] = "other"
    pair = {"arms": {"autolean": auto, "vanilla": vanilla}}
    assert paired_summary([pair])["invalid"] == 1
    assert pair["comparable"] is False


def test_provider_failure_is_not_a_proof_loss() -> None:
    auto, vanilla = _arm(accepted=True), _arm(accepted=False)
    vanilla["calls"] = [{"error": "offline"}]
    pair = {"arms": {"autolean": auto, "vanilla": vanilla}}
    assert paired_summary([pair])["invalid"] == 1


def test_vanilla_retries_receive_no_kernel_feedback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "lakefile.toml").write_text('name = "test"\n')
    source_file = tmp_path / "Test.lean"
    source = "theorem target : True := by\n  sorry\n"
    source_file.write_text(source)
    project = LeanProject(tmp_path)
    monkeypatch.setattr(project, "get_goal_via_hole_punch", lambda *args, **kwargs: "⊢ True")
    monkeypatch.setattr(
        project,
        "validate_candidate",
        lambda *args, **kwargs: BuildResult(
            success=False,
            diagnostics=[Diagnostic("Test.lean", 2, 2, "error", "deliberate rejection")],
        ),
    )
    backend = RecordedBackend(
        Replies([LLMResponse("by trivial", "fixed-model")] * 2), 2, tmp_path / "calls.jsonl"
    )
    checks: list[dict[str, object]] = []
    accepted = _vanilla(project, source_file, backend, "e" * 64, ProgramConfig(), 2, checks)
    assert not accepted
    assert len(checks) == 2
    assert all(check["proof"] == "trivial" and check["axioms"] is None for check in checks)
    assert backend.calls[0]["user"] == backend.calls[1]["user"]
    assert "deliberate rejection" not in str(backend.calls[1]["user"])
    assert source_file.read_text() == source


def test_suite_rejects_repeated_case_identity(tmp_path: Path) -> None:
    path = tmp_path / "suite.json"
    case = {"id": "one", "declaration": "target", "source": "theorem target : True := by sorry"}
    path.write_text(json.dumps({"schema": "autolean-comparison-suite-v1", "cases": [case, case]}))
    with pytest.raises(ValueError, match="unique"):
        load_cases(path)
