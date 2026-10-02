"""Compare AutoLean with independent, plain prompts to the same model.

Run with ``python -m scripts.compare_provers --help`` from the checkout.
Every case and arm owns a fresh project. Model calls share an upper bound;
input usage and elapsed time are reported separately. The suite defines the
population to which any observed difference applies.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import random
import re
import sys
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Literal

from autolean.agent import AutoLeanAgent, clean_llm_proof
from autolean.generated_code import GeneratedCodeError, validate_generated_proof
from autolean.lean_interface import LeanProject
from autolean.llm import BaseBackend, LLMBackend, LLMConfig, LLMError, LLMResponse, create_llm_client
from autolean.program import ProgramConfig, parse_program
from autolean.provenance import ProofEnvironmentError, sha256_text
from autolean.scanner import count_sorries, scan_file
from autolean.tracker import GitError, Outcome
from scripts.lean_workspace import clear_runtime_state, copy_workspace, initialize_repository

Arm = Literal["autolean", "vanilla"]
_SOURCE_FILE = "AutoLeanComparison.lean"
_VANILLA_SYSTEM = (
    "You are a Lean 4 proof assistant. Fill the single sorry in the supplied "
    "source. Return only its replacement proof body, without Markdown or "
    "explanation. Preserve the statement and use no placeholders or new axioms."
)


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


@dataclass(frozen=True)
class Case:
    id: str
    declaration: str
    source: str

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or re.fullmatch(r"[a-zA-Z0-9_-]+", self.id) is None:
            raise ValueError("case id must contain letters, digits, underscores, or hyphens")
        if not isinstance(self.declaration, str) or not self.declaration.strip():
            raise ValueError("case declaration must name one theorem")
        if not isinstance(self.source, str) or count_sorries(self.source) != 1:
            raise ValueError("each case must contain exactly one sorry")


def load_cases(path: Path) -> list[Case]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != "autolean-comparison-suite-v1":
        raise ValueError("expected an autolean-comparison-suite-v1 object")
    rows = payload.get("cases")
    if not isinstance(rows, list) or not rows:
        raise ValueError("the comparison suite must contain cases")
    cases = [Case(**row) for row in rows]
    if len({case.id for case in cases}) != len(cases):
        raise ValueError("case ids must be unique")
    return cases


class RecordedBackend(BaseBackend):
    """Bound calls, freeze sampling, and retain every exact exchange."""

    def __init__(self, backend: LLMBackend, max_calls: int, path: Path) -> None:
        if isinstance(max_calls, bool) or not isinstance(max_calls, int) or max_calls < 1:
            raise ValueError("model-call budget must be positive")
        super().__init__(backend.config, replace(backend.capabilities, retry_temperature=False))
        self.backend = backend
        self.max_calls = max_calls
        self.path = path
        self.calls: list[dict[str, object]] = []
        self.exhausted = False

    def ping(self) -> bool:
        return self.backend.ping()

    def close(self) -> None:
        self.backend.close()

    def generate(
        self,
        system: str,
        user: str,
        *,
        temperature: float | None = None,
        stop: list[str] | None = None,
    ) -> LLMResponse:
        if len(self.calls) >= self.max_calls:
            self.exhausted = True
            raise LLMError("comparison model-call budget exhausted")
        started = time.monotonic()
        call: dict[str, object] = {
            "call": len(self.calls) + 1,
            "system": system,
            "user": user,
            "request_sha256": sha256_text(f"{system}\0{user}"),
            "temperature": self.config.temperature,
            "stop": stop,
        }
        self.calls.append(call)
        try:
            response = self.backend.generate(system, user, temperature=self.config.temperature, stop=stop)
            call.update(asdict(response), response_sha256=sha256_text(response.text))
            if self.capabilities.output_limit and response.output_tokens > self.config.max_output_tokens:
                raise LLMError("provider reported output beyond the configured ceiling")
            return response
        except LLMError as error:
            call["error"] = str(error)
            raise
        finally:
            call["wall_seconds"] = time.monotonic() - started
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(call, ensure_ascii=False) + "\n")


def _source_manifest(root: Path) -> dict[str, str]:
    """Identify source and configuration without build or research state."""
    excluded = {".git", ".lake", ".autolean", ".codedb", "logs", "skills", "training_data", "__pycache__"}
    result = {}
    for directory, subdirs, files in os.walk(root):
        subdirs[:] = sorted(name for name in subdirs if name not in excluded)
        for name in sorted(files):
            path = Path(directory) / name
            if path.suffix in {".lean", ".py", ".toml"} or name in {"lean-toolchain", "lake-manifest.json"}:
                result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _write_program(path: Path, config: ProgramConfig, model: LLMConfig, attempts: int) -> None:
    values: dict[str, object] = {
        "model": model.model,
        "backend": model.backend,
        "endpoint": model.base_url,
        "effort": model.effort,
        "temperature": model.temperature,
        "max_output_tokens": config.max_output_tokens,
        "llm_timeout_seconds": model.timeout,
        "max_retries_per_sorry": attempts,
        "max_cycles": attempts,
        "cycle_timeout_seconds": config.cycle_timeout_seconds,
        "max_proof_lines": config.max_proof_lines,
        "search_scope": "local",
        "escalation_policy": "never",
    }
    for value in values.values():
        if isinstance(value, str) and ("\n" in value or "\r" in value):
            raise ValueError("comparison settings must fit on one line")
    settings = "\n".join(f"{key}: {value}" for key, value in values.items() if value is not None)
    path.write_text(
        f"## Lean Project Path\n\nworkspace\n\n## LLM Configuration\n\n{settings}\n",
        encoding="utf-8",
    )


def _vanilla(
    project: LeanProject,
    source_file: Path,
    backend: RecordedBackend,
    environment: str,
    config: ProgramConfig,
    attempts: int,
    checks: list[dict[str, object]],
) -> bool:
    source = source_file.read_text(encoding="utf-8")
    target = scan_file(source_file)[0]
    goal = project.get_goal_via_hole_punch(
        source_file, target.line, target.col, timeout=config.cycle_timeout_seconds
    )
    user = f"Source:\n```lean\n{source}\n```\n\nGoal:\n{goal or '(unavailable)'}"
    for attempt in range(1, attempts + 1):
        response = backend.generate(_VANILLA_SYSTEM, user)
        check: dict[str, object] = {"attempt": attempt, "accepted": False}
        checks.append(check)
        try:
            proof = validate_generated_proof(clean_llm_proof(response.text, tactic_mode=target.tactic_mode))
            if len(proof.splitlines()) > config.max_proof_lines:
                raise GeneratedCodeError("proof exceeds the configured line limit")
            candidate = project.replace_sorry_at(
                source_file, target.line, proof, original_content=source, col=target.col
            )
            build = project.validate_candidate(
                source_file,
                candidate,
                timeout=config.cycle_timeout_seconds,
                declaration=target.qualified_decl_name,
                declaration_line=target.line,
                expected_environment=environment,
            )
            check.update(
                proof=proof,
                candidate_sha256=sha256_text(candidate),
                build_seconds=build.duration_seconds,
                diagnostics=[asdict(item) for item in build.diagnostics],
                stderr=build.stderr,
                axioms=None if build.axioms is None else list(build.axioms),
            )
            if build.success and count_sorries(candidate) == 0:
                project.write_file(source_file, candidate, expected_content=source)
                check["accepted"] = True
                return True
        except (GeneratedCodeError, ValueError) as error:
            check["error"] = str(error)
    return False


def run_arm(
    arm: Arm,
    directory: Path,
    template: Path,
    case: Case,
    config: ProgramConfig,
    model: LLMConfig,
    *,
    calls: int,
    attempts: int,
) -> dict[str, object]:
    directory.mkdir(parents=True)
    workspace = directory / "workspace"
    copy_workspace(template, workspace)
    clear_runtime_state(workspace)
    source_file = workspace / _SOURCE_FILE
    if source_file.exists():
        raise ValueError(f"template reserves {_SOURCE_FILE} for comparison cases")
    source_file.write_text(case.source, encoding="utf-8")
    targets = scan_file(source_file)
    if len(targets) != 1 or targets[0].qualified_decl_name != case.declaration:
        raise ValueError(f"case {case.id!r} does not select one named declaration")
    initial_manifest = _source_manifest(workspace)
    initialize_repository(workspace)
    program = directory / "program.md"
    _write_program(program, config, model, attempts)
    result: dict[str, object] = {
        "arm": arm,
        "case": case.id,
        "source_sha256": sha256_text(case.source),
        "project_source_sha256": sha256_text(json.dumps(initial_manifest, sort_keys=True)),
        "accepted": False,
        "status": "incomplete",
        "checks": [],
        "calls": [],
    }
    started = time.monotonic()
    backend: RecordedBackend | None = None
    try:
        project = LeanProject(workspace)
        environment = project.proof_environment()
        result["environment_sha256"] = environment.sha256
        baseline = project.check_file(source_file, timeout=config.cycle_timeout_seconds, untrusted=True)
        if not baseline.success:
            raise ValueError(f"case source does not elaborate: {baseline.stderr or baseline.errors}")
        backend = RecordedBackend(create_llm_client(model), calls, directory / "calls.jsonl")
        result["output_ceiling_per_call"] = (
            model.max_output_tokens if backend.capabilities.output_limit else None
        )
        result["token_reporting_supported"] = backend.capabilities.token_counts
        if not backend.ping():
            raise LLMError("provider preflight failed")
        if arm == "vanilla":
            checks: list[dict[str, object]] = []
            result["checks"] = checks
            accepted = _vanilla(project, source_file, backend, environment.sha256, config, attempts, checks)
            result.update(accepted=accepted, checks=checks, status="completed")
        else:
            agent = AutoLeanAgent(program, target_file=source_file, backend_factory=lambda _: backend)
            agent.project = project
            run = agent.run()
            if run.message == "Agent interrupted before completion.":
                result["status"] = "interrupted"
                raise KeyboardInterrupt
            accepted = (
                any(
                    record.outcome is Outcome.SUCCESS
                    and record.decl_name == targets[0].decl_name
                    and record.file == _SOURCE_FILE
                    for record in agent.tracker.records
                )
                and count_sorries(source_file.read_text(encoding="utf-8")) == 0
            )
            strategy_rejected = any(
                record.error_category == "strategy_generation" for record in agent.tracker.records
            ) and all("error" not in call for call in backend.calls)
            result.update(
                accepted=accepted,
                checks=[record.as_dict() for record in agent.tracker.records],
                status="completed" if run.successful or strategy_rejected else "incomplete",
                error=run.message,
            )
    except (LLMError, OSError, ValueError, GitError, ProofEnvironmentError) as error:
        result["error"] = str(error)
    finally:
        if backend is not None:
            result["calls"] = backend.calls
            if backend.exhausted:
                result["status"] = "budget_exhausted"
            if model.model_revision is not None and not backend.ping():
                result.update(status="incomplete", error="model revision failed its final preflight")
            backend.close()
        result["wall_seconds"] = time.monotonic() - started
        result["final_source_sha256"] = sha256_text(source_file.read_text(encoding="utf-8"))
        _write_json(directory / "result.json", result)
    return result


def paired_summary(pairs: list[dict[str, object]]) -> dict[str, object]:
    wins = {"autolean": 0, "vanilla": 0, "both": 0, "neither": 0, "invalid": 0}
    for pair in pairs:
        arms = pair["arms"]
        assert isinstance(arms, dict)
        auto, vanilla = arms["autolean"], arms["vanilla"]
        comparable = all(
            auto.get(key) and auto.get(key) == vanilla.get(key)
            for key in ("source_sha256", "project_source_sha256", "environment_sha256")
        )
        models = {call["model"] for result in arms.values() for call in result["calls"] if "model" in call}
        comparable = (
            comparable
            and len(models) <= 1
            and all(result["status"] in {"completed", "budget_exhausted"} for result in arms.values())
            and all("error" not in call for result in arms.values() for call in result["calls"])
        )
        pair["comparable"] = comparable
        if not comparable:
            wins["invalid"] += 1
        elif auto["accepted"] and vanilla["accepted"]:
            wins["both"] += 1
        elif auto["accepted"]:
            wins["autolean"] += 1
        elif vanilla["accepted"]:
            wins["vanilla"] += 1
        else:
            wins["neither"] += 1
    return wins


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--program", type=Path, default=Path("program.md"))
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--calls", type=int, default=4, help="model requests per case and arm, including planning"
    )
    parser.add_argument("--attempts", type=int, default=4, help="proof attempts per case and arm")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--model-revision", help="require this Ollama model digest before and after each arm")
    parser.add_argument(
        "--order-seed", type=int, default=0, help="shuffle arm order; independent of model sampling"
    )
    options = parser.parse_args()
    if min(options.calls, options.attempts, options.repeats) < 1:
        parser.error("calls, attempts, and repeats must be positive")
    config = parse_program(options.program)
    model = config.llm_config()
    model = replace(model, fallbacks=False, model_revision=options.model_revision or model.model_revision)
    if model.model_revision and model.backend != "ollama":
        parser.error("model-revision checks currently require the Ollama provider")
    template = (options.program.resolve().parent / config.lean_project_path).resolve()
    output = options.output.resolve()
    if output.is_relative_to(template):
        parser.error("output must be outside the template project")
    cases = load_cases(options.suite)
    output.mkdir(parents=True, exist_ok=False)
    source_root = Path(__file__).resolve().parents[1]
    implementation = {
        **{f"autolean/{key}": value for key, value in _source_manifest(source_root / "autolean").items()},
        **{
            f"scripts/{name}": hashlib.sha256((source_root / "scripts" / name).read_bytes()).hexdigest()
            for name in ("compare_provers.py", "lean_workspace.py")
        },
    }
    _write_json(output / "implementation.json", implementation)
    report: dict[str, object] = {
        "schema": "autolean-comparison-v1",
        "runtime": {
            "python": sys.version,
            "platform": platform.platform(),
            "packages": {
                distribution.metadata["Name"]: distribution.version
                for distribution in importlib.metadata.distributions()
                if distribution.metadata["Name"]
            },
        },
        "model": asdict(model),
        "settings": asdict(config)
        | {
            "search_scope": "local",
            "escalation_policy": "never",
            "max_cycles": options.attempts,
            "max_retries_per_sorry": options.attempts,
            "goals": [],
            "constraints": [],
            "strategy_hints": [],
        },
        "calls_per_arm": options.calls,
        "attempts_per_arm": options.attempts,
        "order_seed": options.order_seed,
        "suite_sha256": hashlib.sha256(options.suite.read_bytes()).hexdigest(),
        "implementation_sha256": sha256_text(json.dumps(implementation, sort_keys=True)),
        "pairs": [],
    }
    _write_json(
        output / "suite.json", {"schema": "autolean-comparison-suite-v1", "cases": [asdict(c) for c in cases]}
    )
    pairs: list[dict[str, object]] = []
    randomizer = random.Random(options.order_seed)
    orders: dict[str, list[Arm]] = {}
    for case in cases:
        orders[case.id] = ["autolean", "vanilla"]
        randomizer.shuffle(orders[case.id])
    for repetition in range(options.repeats):
        for case in cases:
            order = orders[case.id][:: -1 if repetition % 2 else 1]
            arms = {
                arm: run_arm(
                    arm,
                    output / f"{repetition + 1}-{case.id}" / arm,
                    template,
                    case,
                    config,
                    model,
                    calls=options.calls,
                    attempts=options.attempts,
                )
                for arm in order
            }
            pairs.append({"case": case.id, "repetition": repetition + 1, "order": order, "arms": arms})
            report.update(pairs=pairs, summary=paired_summary(pairs))
            _write_json(output / "comparison.json", report)
    print(json.dumps(report["summary"], sort_keys=True))
    return 0 if all(pair["comparable"] for pair in pairs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
