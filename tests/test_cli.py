"""Comprehensive CLI interface tests — prove every public command works."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import click
import pytest
from click.testing import CliRunner

from autolean.__main__ import AUTOLEAN_BANNER, _run_agent, main
from autolean.routing import EscalationPolicy

PYTHAGOREAN_FORMALIZATION = (
    "open RealInnerProductSpace\n\n"
    "theorem pythagorean_theorem "
    "{V : Type*} [NormedAddCommGroup V] "
    "[InnerProductSpace ℝ V] (x y : V) "
    "(h : ⟪x, y⟫ = 0) :\n"
    "    ‖x + y‖ ^ 2 = ‖x‖ ^ 2 + ‖y‖ ^ 2 := by\n"
    "  sorry"
)


def _registered_command_paths(
    group: click.Group,
    prefix: tuple[str, ...] = (),
) -> list[tuple[str, ...]]:
    paths: list[tuple[str, ...]] = []
    for name, command in sorted(group.commands.items()):
        path = (*prefix, name)
        paths.append(path)
        if isinstance(command, click.Group):
            paths.extend(_registered_command_paths(command, path))
    return paths


@pytest.mark.parametrize(
    "command",
    [
        ("solve",),
        ("prove",),
        ("resume",),
        ("problems", "work"),
    ],
)
def test_proof_workflows_share_model_escalation_grammar(
    runner: CliRunner,
    command: tuple[str, ...],
) -> None:
    result = runner.invoke(main, [*command, "--help"])

    assert result.exit_code == 0
    assert "--escalation" in result.output
    assert "--escalate-to" in result.output
    assert "--escalate-after" in result.output


@pytest.mark.parametrize("command_path", _registered_command_paths(main))
def test_every_registered_command_has_a_help_smoke_test(
    runner: CliRunner,
    command_path: tuple[str, ...],
) -> None:
    result = runner.invoke(main, [*command_path, "--help"])

    assert result.exit_code == 0
    assert "Usage:" in result.output


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """Create a minimal AutoLean project for testing."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    al = ws / "AutoLean"
    al.mkdir()

    # lakefile.lean
    (ws / "lakefile.lean").write_text(
        "import Lake\nopen Lake DSL\n"
        "package test_ws\n"
        '@[default_target]\nlean_lib AutoLean where srcDir := "."\n'
    )
    (ws / "lean-toolchain").write_text("leanprover/lean4:v4.33.0\n")

    # A lean file with sorrys
    (al / "Test.lean").write_text(
        "theorem test_rfl : 1 + 1 = 2 := by\n  sorry\n\n"
        "theorem test_impl (P Q : Prop) (h : P) (f : P -> Q) : Q := by\n  sorry\n"
    )
    (ws / "AutoLean.lean").write_text("import AutoLean.Test\n")

    # program.md
    (tmp_path / "program.md").write_text(
        "# Test\n\n## Mode\nsorry-elimination\n\n"
        "## Lean Project Path\nworkspace\n\n"
        "## LLM Configuration\nmodel: gemma4:26b\ntemperature: 0.4\n"
        "max_retries_per_sorry: 3\ncycle_timeout_seconds: 60\nmax_cycles: 0\n"
    )

    return tmp_path


# ---------------------------------------------------------------------------
# Version and help
# ---------------------------------------------------------------------------


class TestCLIBasics:
    def test_version(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert "0.5.0" in result.output or "autolean" in result.output

    def test_help(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert result.output.startswith(AUTOLEAN_BANNER)
        assert "prove" in result.output
        assert "verify" in result.output
        assert "solve" in result.output
        assert "targets" in result.output
        assert "inspect" in result.output
        assert "workbench" in result.output
        assert "doctor" in result.output
        assert "models" in result.output
        assert "improve" in result.output
        assert "Proof workflows" in result.output
        assert "Understand" in result.output

    def test_help_colors_the_banner_in_a_terminal(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["--help"], color=True)

        assert result.exit_code == 0
        assert result.output.startswith("\x1b[36m\x1b[1m")
        assert AUTOLEAN_BANNER in result.output

    def test_run_help(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["solve", "--help"])
        assert result.exit_code == 0
        assert "--max-cycles" in result.output
        assert "--model" in result.output
        assert "--resume" in result.output

    @pytest.mark.parametrize(
        ("alias", "canonical"),
        [
            ("run", "solve"),
            ("scan", "targets"),
            ("check", "doctor"),
            ("diff", "changes"),
            ("ui", "workbench"),
        ],
    )
    def test_compatibility_aliases_resolve(
        self,
        runner: CliRunner,
        alias: str,
        canonical: str,
    ) -> None:
        alias_help = runner.invoke(main, [alias, "--help"])
        canonical_help = runner.invoke(main, [canonical, "--help"])

        assert alias_help.exit_code == 0
        assert canonical_help.exit_code == 0
        assert alias_help.output.splitlines()[1:] == canonical_help.output.splitlines()[1:]

    def test_prove_help(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["prove", "--help"])
        assert result.exit_code == 0
        assert AUTOLEAN_BANNER not in result.output
        assert "STATEMENT" in result.output
        assert "--max-attempts" in result.output
        assert "--formalization-repairs" in result.output
        assert "--review-plan" in result.output

    @pytest.mark.parametrize(
        ("statement", "lean_code", "declaration"),
        [
            (
                "1 + 1 = 2",
                "theorem one_add_one_eq_two : (1 : Nat) + 1 = 2 := by\n  sorry",
                "one_add_one_eq_two",
            ),
            (
                "the pythagorean theorem",
                PYTHAGOREAN_FORMALIZATION,
                "pythagorean_theorem",
            ),
            (
                "the pytahgorean theorem",
                PYTHAGOREAN_FORMALIZATION,
                "pythagorean_theorem",
            ),
        ],
    )
    def test_prove_isolates_a_new_theorem_from_unrelated_source_errors(
        self,
        runner: CliRunner,
        project_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
        statement: str,
        lean_code: str,
        declaration: str,
    ) -> None:
        from autolean.llm import LLMResponse

        workspace = project_dir / "workspace"
        shared = workspace / "AutoLean" / "UserTheorems.lean"
        unrelated = "theorem riemann_hypothesis (s : ℂ) (n : ℕ) : s = n := by\n  sorry\n"
        shared.write_text(unrelated, encoding="utf-8")
        responses = iter(
            [
                json.dumps(
                    {
                        "objective": "Prove the equality in Nat.",
                        "formalization": ["Fix the numeral type to Nat."],
                        "observations": ["The equality computes."],
                        "invariants": ["Preserve the exact theorem statement."],
                        "obstructions": ["Reject an inferred non-Nat numeral type."],
                        "reductions": ["Normalize both sides."],
                        "premises": ["Use Mathlib arithmetic normalization."],
                        "methods": ["Try norm_num."],
                        "partial_results": [],
                        "risks": ["Numerals are polymorphic."],
                        "completion_criteria": ["Lean accepts the theorem without placeholders."],
                        "checkpoints": ["Compile the scaffold."],
                        "revision_triggers": ["Lean infers a different numeral type."],
                    }
                ),
                lean_code,
            ]
        )

        class Backend:
            config = SimpleNamespace(model="fixture", backend="fixture")

            def __enter__(self) -> Backend:
                return self

            def __exit__(self, *exc: object) -> None:
                return None

            def generate(self, system: str, user: str) -> LLMResponse:
                del system, user
                return LLMResponse(text=next(responses), model="fixture")

        class Project:
            def __init__(self, root: Path) -> None:
                self.root = root.resolve()

            def validate_candidate(
                self,
                path: Path,
                source: str,
                **kwargs: object,
            ) -> SimpleNamespace:
                del path, source, kwargs
                return SimpleNamespace(success=True, errors=[], stderr="", stdout="")

            def accept_candidate(
                self,
                path: Path,
                source: str,
                **kwargs: object,
            ) -> SimpleNamespace:
                del kwargs
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(source, encoding="utf-8")
                return SimpleNamespace(success=True, errors=[], stderr="", stdout="")

        captured: dict[str, object] = {}

        class Agent:
            config = SimpleNamespace(
                max_cycles=0,
                max_retries_per_sorry=5,
                strategy_hints=[],
                escalation_policy=EscalationPolicy.ASK,
                escalation_model=None,
                escalation_after_failures=2,
            )
            model_transitions: tuple[object, ...] = ()
            project = SimpleNamespace(root=workspace)
            llm = SimpleNamespace(
                config=SimpleNamespace(model="fixture", backend="fixture"),
            )

            def run(self) -> SimpleNamespace:
                return SimpleNamespace(successful=True, message="")

            def close(self) -> None:
                return None

        def agent_for(*args: object, **kwargs: object) -> Agent:
            del args
            captured.update(kwargs)
            return Agent()

        monkeypatch.setattr("autolean.__main__._connected_llm", lambda *args, **kwargs: Backend())
        monkeypatch.setattr("autolean.__main__._agent_for", agent_for)
        monkeypatch.setattr("autolean.lean_interface.LeanProject", Project)

        result = runner.invoke(
            main,
            [
                "prove",
                statement,
                "--program",
                str(project_dir / "program.md"),
            ],
        )

        assert result.exit_code == 0
        generated_files = list((workspace / "AutoLean" / "Generated").glob("*.lean"))
        assert len(generated_files) == 1
        generated = generated_files[0]
        assert generated.is_file()
        generated_source = generated.read_text(encoding="utf-8")
        assert f"theorem {declaration}" in generated_source
        assert "riemann_hypothesis" not in generated_source
        assert shared.read_text(encoding="utf-8") == unrelated
        assert captured["target_file"] == generated
        assert "Mathematical research plan" in result.output
        assert "Formalization compiled" in result.output
        assert "autolean resume" in result.output

    def test_verify_help(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["verify", "--help"])
        assert result.exit_code == 0
        assert "SOURCE" in result.output
        assert "--pages" in result.output

    def test_improve_help(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["improve", "--help"])
        assert result.exit_code == 0
        assert "FILE_PATH" in result.output
        assert "THEOREM_NAME" in result.output
        assert "--goal" in result.output
        assert "shorter" in result.output
        assert "elegant" in result.output

    def test_models_help(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["models", "--help"])
        assert result.exit_code == 0

    def test_environment_help(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["environment", "--help"])
        assert result.exit_code == 0
        assert "--json" in result.output

    def test_init_help(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["init", "--help"])
        assert result.exit_code == 0
        assert "--mathlib" in result.output
        assert "--cslib" in result.output

    def test_finetune_config_help(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["finetune-config", "--help"])
        assert result.exit_code == 0
        assert "--framework" in result.output
        assert "axolotl" in result.output

    def test_export_training_help(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["export-training", "--help"])
        assert result.exit_code == 0

    def test_diff_help(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["diff", "--help"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Scan command (works without LLM)
# ---------------------------------------------------------------------------


class TestScanCommand:
    def test_scan_finds_targets(self, runner: CliRunner, project_dir: Path) -> None:
        result = runner.invoke(main, ["scan", "-d", str(project_dir / "workspace")])
        assert result.exit_code == 0
        assert "sorry target" in result.output
        assert "test_rfl" in result.output
        assert "test_impl" in result.output

    def test_scan_json_output(self, runner: CliRunner, project_dir: Path) -> None:
        result = runner.invoke(main, ["scan", "-d", str(project_dir / "workspace"), "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 2
        names = {d["decl_name"] for d in data}
        assert "test_rfl" in names
        assert "test_impl" in names

    def test_scan_json_has_tactic_mode(self, runner: CliRunner, project_dir: Path) -> None:
        result = runner.invoke(main, ["scan", "-d", str(project_dir / "workspace"), "--json"])
        data = json.loads(result.output)
        for item in data:
            assert "tactic_mode" in item
            assert "difficulty" in item

    def test_scan_empty_project(self, runner: CliRunner, tmp_path: Path) -> None:
        ws = tmp_path / "empty"
        ws.mkdir()
        (ws / "lakefile.lean").write_text("import Lake\nopen Lake DSL\npackage empty\n")
        (ws / "lean-toolchain").write_text("leanprover/lean4:v4.29.0\n")
        result = runner.invoke(main, ["scan", "-d", str(ws)])
        assert result.exit_code == 0
        assert "0 sorry" in result.output

    def test_targets_json_format(self, runner: CliRunner, project_dir: Path) -> None:
        result = runner.invoke(
            main,
            ["targets", "-d", str(project_dir / "workspace"), "--format", "json"],
        )

        assert result.exit_code == 0
        assert len(json.loads(result.output)) == 2


class TestInspectCommand:
    def test_inspect_shows_structural_context(self, runner: CliRunner, project_dir: Path) -> None:
        result = runner.invoke(
            main,
            ["inspect", "test_rfl", "-d", str(project_dir / "workspace")],
        )

        assert result.exit_code == 0
        assert "test_rfl" in result.output
        assert "parse_quality" in result.output
        assert "syntax_path" in result.output
        assert "source_sha256" in result.output

    def test_inspect_json_is_machine_readable(self, runner: CliRunner, project_dir: Path) -> None:
        result = runner.invoke(
            main,
            [
                "inspect",
                "test_impl",
                "-d",
                str(project_dir / "workspace"),
                "--format",
                "json",
            ],
        )

        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["target"]["declaration"] == "test_impl"
        assert payload["structure"]["target"]["name"] == "test_impl"
        assert len(payload["structure"]["context_sha256"]) == 64


def test_environment_json_reports_complete_identity(
    runner: CliRunner,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autolean.provenance import ProofEnvironment

    environment = ProofEnvironment(
        sha256="a" * 64,
        lean_version="Lean (version 4.33.0)",
        lean_toolchain="leanprover/lean4:v4.33.0",
        manifest_sha256="b" * 64,
        artifact_count=42,
        dependencies=(f"mathlib@{'c' * 40}",),
    )

    class Project:
        def __init__(self, root: Path) -> None:
            self.root = root

        def proof_environment(self, *, refresh: bool = False) -> ProofEnvironment:
            assert refresh
            return environment

    monkeypatch.setattr("autolean.lean_interface.LeanProject", Project)

    result = runner.invoke(
        main,
        ["environment", "-d", str(project_dir / "workspace"), "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["sha256"] == "a" * 64
    assert payload["artifact_count"] == 42


def test_doctor_validates_an_inline_markdown_proof(
    runner: CliRunner,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autolean.llm import BaseBackend, LLMConfig, LLMResponse
    from autolean.provenance import ProofEnvironment

    class Backend(BaseBackend):
        def ping(self) -> bool:
            return True

        def generate(
            self,
            system: str,
            user: str,
            *,
            temperature: float | None = None,
            stop: list[str] | None = None,
        ) -> LLMResponse:
            return LLMResponse(text="`trivial`", model=self.config.model)

    class Project:
        checked_source = ""

        def __init__(self, root: Path) -> None:
            self.root = root.resolve()

        def lean_files(self) -> list[Path]:
            return [self.root / "AutoLean" / "Test.lean"]

        def proof_environment(self) -> ProofEnvironment:
            return ProofEnvironment(
                sha256="a" * 64,
                lean_version="Lean (version 4.33.0)",
                lean_toolchain="leanprover/lean4:v4.33.0",
                manifest_sha256="b" * 64,
                artifact_count=42,
                dependencies=(f"mathlib@{'c' * 40}",),
            )

        def validate_candidate(self, path: Path, source: str, **kwargs: object) -> SimpleNamespace:
            Project.checked_source = source
            return SimpleNamespace(success=True, duration_seconds=0.1, stderr="", errors=[])

        def build(self, *, timeout: int) -> SimpleNamespace:
            return SimpleNamespace(success=True, duration_seconds=0.1, errors=[])

    backend = Backend(LLMConfig(model="test", backend="ollama"))
    monkeypatch.setattr("autolean.__main__._llm_for", lambda *args, **kwargs: backend)
    monkeypatch.setattr("autolean.lean_interface.LeanProject", Project)

    result = runner.invoke(main, ["doctor", "--program", str(project_dir / "program.md")])

    assert result.exit_code == 0
    assert "Proof SHA-256" in result.output
    assert "Lean kernel candidate" in result.output
    assert "theorem AutoLeanBackendSmoke : True := by" in result.output
    assert "Model proof passed sandboxed Lean" in result.output
    assert "  trivial" in Project.checked_source


# ---------------------------------------------------------------------------
# Init command
# ---------------------------------------------------------------------------


class TestInitCommand:
    def test_init_creates_project(self, runner: CliRunner, tmp_path: Path) -> None:
        target = tmp_path / "my_project"
        result = runner.invoke(main, ["init", str(target)])
        assert result.exit_code == 0
        assert (target / "lakefile.lean").exists()
        assert (target / "lean-toolchain").exists()
        assert (target / "my_project.lean").exists()
        # Check lakefile content
        lakefile = (target / "lakefile.lean").read_text()
        assert "my_project" in lakefile
        assert "mathlib4" in lakefile
        assert "leanprover/cslib" in lakefile
        source = (target / "my_project.lean").read_text()
        assert "import Mathlib" in source
        assert "import Cslib" in source

    def test_init_with_mathlib(self, runner: CliRunner, tmp_path: Path) -> None:
        target = tmp_path / "math_proj"
        result = runner.invoke(main, ["init", str(target), "--mathlib"])
        assert result.exit_code == 0
        lakefile = (target / "lakefile.lean").read_text()
        assert "mathlib" in lakefile
        assert "v4.33.0" in lakefile

    def test_init_with_cslib(self, runner: CliRunner, tmp_path: Path) -> None:
        target = tmp_path / "cs_project"
        result = runner.invoke(main, ["init", str(target), "--cslib"])
        assert result.exit_code == 0
        lakefile = (target / "lakefile.lean").read_text()
        assert "leanprover/cslib" in lakefile
        assert "v4.33.0" in lakefile

    def test_init_can_select_lean_core_only(self, runner: CliRunner, tmp_path: Path) -> None:
        target = tmp_path / "core_project"
        result = runner.invoke(
            main,
            ["init", str(target), "--no-mathlib", "--no-cslib"],
        )
        assert result.exit_code == 0
        lakefile = (target / "lakefile.lean").read_text()
        assert "require mathlib" not in lakefile
        assert "require cslib" not in lakefile

    def test_init_sanitizes_a_common_directory_name(self, runner: CliRunner, tmp_path: Path) -> None:
        target = tmp_path / "my-project"
        result = runner.invoke(main, ["init", str(target)])
        assert result.exit_code == 0
        assert (target / "my_project.lean").is_file()
        assert "lean_lib my_project" in (target / "lakefile.lean").read_text(encoding="utf-8")

    def test_init_refuses_to_overwrite_managed_files(self, runner: CliRunner, tmp_path: Path) -> None:
        target = tmp_path / "existing"
        target.mkdir()
        lakefile = target / "lakefile.lean"
        lakefile.write_text("user configuration\n", encoding="utf-8")

        result = runner.invoke(main, ["init", str(target)])

        assert result.exit_code != 0
        assert "Refusing to overwrite" in result.output
        assert lakefile.read_text(encoding="utf-8") == "user configuration\n"


# ---------------------------------------------------------------------------
# Diff command
# ---------------------------------------------------------------------------


class TestDiffCommand:
    def test_diff_handles_a_new_repository_and_uncommitted_source(
        self,
        runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        project = tmp_path / "project"
        project.mkdir()
        source = project / "Example.lean"
        source.write_text("theorem example : True := by\n  trivial\n", encoding="utf-8")
        git = ["git", "-c", "core.fsmonitor=false"]
        subprocess.run([*git, "init", "-q"], cwd=project, check=True)
        subprocess.run([*git, "add", "Example.lean"], cwd=project, check=True)
        subprocess.run(
            [
                *git,
                "-c",
                "user.name=AutoLean Tests",
                "-c",
                "user.email=tests@autolean.invalid",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-q",
                "-m",
                "proof: Prove example",
            ],
            cwd=project,
            check=True,
        )
        source.write_text("theorem example : True := by\n  exact True.intro\n", encoding="utf-8")

        result = runner.invoke(main, ["diff", "-d", str(project)])

        assert result.exit_code == 0
        assert "Uncommitted Lean changes" in result.output
        assert "Example.lean" in result.output
        assert "1 recent proofs" in result.output
        assert "example" in result.output


# ---------------------------------------------------------------------------
# Models command (works without LLM if Ollama is down)
# ---------------------------------------------------------------------------


class TestModelsCommand:
    @pytest.fixture(autouse=True)
    def automatic_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from autolean.models import PROFILES

        monkeypatch.setattr(
            "autolean.models.detect_default_profile",
            lambda: PROFILES["fable"],
        )

    def test_models_lists_profiles(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["models"])
        # Should complete even if Ollama is down (shows "not installed")
        assert result.exit_code == 0
        assert "gemma4" in result.output
        assert "deepseek-prover" in result.output
        assert "Automatic default" in result.output

    def test_models_shows_setup_commands(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["models"])
        assert result.exit_code == 0
        assert "ollama pull" in result.output

    def test_models_lists_the_subscription_profiles(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["models"])
        assert result.exit_code == 0
        for profile in ("opus", "sonnet", "codex"):
            assert profile in result.output

    def test_models_lists_every_backend(self, runner: CliRunner) -> None:
        from autolean.llm import BACKENDS

        result = runner.invoke(main, ["models"])
        assert result.exit_code == 0
        for name in BACKENDS:
            assert name in result.output


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------


class TestBackendOption:
    """`--model` and `--backend` behave the same on every command."""

    MODEL_COMMANDS: ClassVar[list[str]] = [
        "run",
        "check",
        "prove",
        "verify",
        "build-library",
        "improve",
    ]

    @pytest.mark.parametrize("command", MODEL_COMMANDS)
    def test_command_accepts_model_and_backend(self, runner: CliRunner, command: str) -> None:
        result = runner.invoke(main, [command, "--help"])
        assert result.exit_code == 0
        assert "--model" in result.output
        assert "--backend" in result.output

    def test_backend_choices_come_from_the_registry(self, runner: CliRunner) -> None:
        from autolean.llm import BACKENDS

        result = runner.invoke(main, ["run", "--help"])
        for name in BACKENDS:
            assert name in result.output

    def test_unknown_backend_is_rejected(self, runner: CliRunner, project_dir: Path) -> None:
        result = runner.invoke(
            main,
            ["check", "-p", str(project_dir / "program.md"), "--backend", "telepathy"],
        )
        assert result.exit_code != 0
        assert "telepathy" in result.output

    def test_run_help_documents_overnight(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["run", "--help"])
        assert "--overnight" in result.output


class TestRunPolicy:
    @staticmethod
    def _agent_class(created: list[object]) -> type:
        class FakeAgent:
            def __init__(self, **kwargs: object) -> None:
                program_path = Path(str(kwargs["program_path"]))
                self.config = SimpleNamespace(
                    max_cycles=7,
                    max_retries_per_sorry=3,
                    strategy_hints=[],
                    escalation_policy=EscalationPolicy.ASK,
                    escalation_model=None,
                    escalation_after_failures=2,
                )
                self.model_transitions: tuple[object, ...] = ()
                self.resume = kwargs["resume"]
                self.project = SimpleNamespace(root=program_path.parent / "workspace")
                self.llm = SimpleNamespace(
                    close=lambda: None,
                    config=SimpleNamespace(model="fixture", backend="fixture"),
                )
                created.append(self)

            def run(self) -> SimpleNamespace:
                return SimpleNamespace(successful=True, message="")

            def close(self) -> None:
                self.llm.close()

        return FakeAgent

    def test_regular_run_preserves_program_policy(
        self,
        runner: CliRunner,
        project_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        created: list[object] = []
        monkeypatch.setattr("autolean.agent.AutoLeanAgent", self._agent_class(created))
        result = runner.invoke(main, ["run", "--program", str(project_dir / "program.md")])
        assert result.exit_code == 0
        agent = created[0]
        assert agent.config.max_cycles == 7  # type: ignore[attr-defined]
        assert agent.config.max_retries_per_sorry == 3  # type: ignore[attr-defined]
        assert agent.resume is False  # type: ignore[attr-defined]

    def test_overnight_enables_persistent_run_policy(
        self,
        runner: CliRunner,
        project_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        created: list[object] = []
        monkeypatch.setattr("autolean.agent.AutoLeanAgent", self._agent_class(created))
        result = runner.invoke(
            main,
            ["run", "--program", str(project_dir / "program.md"), "--overnight"],
        )
        assert result.exit_code == 0
        agent = created[0]
        assert agent.config.max_cycles == 0  # type: ignore[attr-defined]
        assert agent.config.max_retries_per_sorry == 100  # type: ignore[attr-defined]
        assert agent.resume is True  # type: ignore[attr-defined]

    def test_terminal_agent_failure_becomes_click_failure(self) -> None:
        class FailedAgent:
            closed = False

            def run(self) -> SimpleNamespace:
                return SimpleNamespace(successful=False, message="quota exhausted")

            def close(self) -> None:
                self.closed = True

        agent = FailedAgent()
        with pytest.raises(click.ClickException, match="quota exhausted"):
            _run_agent(agent)  # type: ignore[arg-type]
        assert agent.closed


def test_problem_scaffold_creates_a_source_fidelity_workspace(
    runner: CliRunner,
    project_dir: Path,
) -> None:
    result = runner.invoke(
        main,
        ["problems", "work", "riemann", "--program", str(project_dir / "program.md")],
    )

    brief = project_dir / "workspace" / "AutoLean" / "Research" / "riemann.md"
    assert result.exit_code == 0
    assert brief.is_file()
    assert "Formalization protocol" in brief.read_text(encoding="utf-8")
    assert "source-faithful Lean statement" in result.output


def test_problem_commands_use_nouns_for_discovery_and_work(runner: CliRunner) -> None:
    result = runner.invoke(main, ["problems", "--help"])

    assert result.exit_code == 0
    for command in ("list", "search", "show", "suggest", "work"):
        assert command in result.output


def test_prove_routes_the_riemann_hypothesis_to_source_research(
    runner: CliRunner,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_model(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("an open-problem match must not query a model")

    monkeypatch.setattr("autolean.__main__._connected_llm", no_model)

    result = runner.invoke(
        main,
        [
            "prove",
            "riemann hypothesis\n",
            "--program",
            str(project_dir / "program.md"),
        ],
    )

    assert result.exit_code == 0
    assert "Recognized curated open problem" in result.output
    assert "source-faithful Lean statement" in result.output
    assert (project_dir / "workspace" / "AutoLean" / "Research" / "riemann.md").is_file()


def test_challenge_reopens_owned_source_as_a_session(
    runner: CliRunner,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = project_dir / "workspace"
    path = workspace / "AutoLean" / "Challenge_Collatz.lean"
    content = (
        "import Mathlib\n\n/-!\n"
        "Generated by: autolean challenge collatz\n"
        "-/\n\ntheorem collatz_remaining : True := by\n  sorry\n"
    )
    path.write_text(content, encoding="utf-8")
    captured: dict[str, object] = {}

    class Agent:
        config = SimpleNamespace(
            max_cycles=0,
            strategy_hints=[],
            escalation_policy=EscalationPolicy.ASK,
            escalation_model=None,
            escalation_after_failures=2,
        )
        model_transitions: tuple[object, ...] = ()
        project = SimpleNamespace(root=workspace)
        llm = SimpleNamespace(
            config=SimpleNamespace(model="fixture", backend="fixture"),
        )

        def run(self) -> SimpleNamespace:
            return SimpleNamespace(successful=True, message="cycle budget reached")

        def close(self) -> None:
            return None

    def agent_for(*args: object, **kwargs: object) -> Agent:
        del args
        captured.update(kwargs)
        return Agent()

    monkeypatch.setattr("autolean.__main__._agent_for", agent_for)

    result = runner.invoke(
        main,
        [
            "challenge",
            "collatz",
            "--max-cycles",
            "3",
            "--program",
            str(project_dir / "program.md"),
        ],
    )

    assert result.exit_code == 0
    assert path.read_text(encoding="utf-8") == content
    assert captured["resume"] is True
    assert captured["target_file"] == path
    assert "Continuing" in result.output
    assert "autolean resume" in result.output
    assert len(list((workspace / ".autolean" / "sessions").glob("*.json"))) == 1


def test_resume_uses_persisted_scope_and_accepts_a_new_model(
    runner: CliRunner,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autolean.session import SessionKind, SessionStore

    workspace = project_dir / "workspace"
    path = workspace / "AutoLean" / "SessionTarget.lean"
    path.write_text("theorem target : True := by\n  sorry\n", encoding="utf-8")
    store = SessionStore(workspace)
    session = store.create(
        kind=SessionKind.THEOREM,
        title="Target theorem",
        model="opus",
        backend="claude_cli",
        max_cycles=5,
        target_file=path,
        target_filter="target",
        session_id="20260811-target-00000001",
    )
    captured: dict[str, object] = {}

    class Agent:
        config = SimpleNamespace(
            max_cycles=0,
            strategy_hints=[],
            escalation_policy=EscalationPolicy.ASK,
            escalation_model=None,
            escalation_after_failures=2,
        )
        model_transitions: tuple[object, ...] = ()
        project = SimpleNamespace(root=workspace)
        llm = SimpleNamespace(
            config=SimpleNamespace(model="sonnet", backend="claude_cli"),
        )

        def run(self) -> SimpleNamespace:
            path.write_text("theorem target : True := by\n  trivial\n", encoding="utf-8")
            return SimpleNamespace(successful=True, message="")

        def close(self) -> None:
            return None

    def agent_for(*args: object, **kwargs: object) -> Agent:
        del args
        captured.update(kwargs)
        return Agent()

    monkeypatch.setattr("autolean.__main__._agent_for", agent_for)

    result = runner.invoke(
        main,
        [
            "resume",
            session.id,
            "--model",
            "sonnet",
            "--max-cycles",
            "2",
            "--guide",
            "Try a direct proof.",
            "--program",
            str(project_dir / "program.md"),
        ],
    )

    assert result.exit_code == 0
    assert captured["model"] == "sonnet"
    assert captured["resume"] is True
    assert captured["target_file"] == path
    completed = store.load(session.id)
    assert completed.status.value == "completed"
    assert completed.max_cycles == 2
    assert completed.guidance == ("Try a direct proof.",)


class TestPaperWorkflow:
    def test_extract_only_does_not_require_a_model(
        self,
        runner: CliRunner,
        project_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from autolean.paper import Claim, PaperDocument

        monkeypatch.setattr(
            "autolean.paper.read_paper",
            lambda source, pages=None, **kwargs: PaperDocument(
                title="Fixture",
                claims=[Claim(label="Theorem 1", statement="True", kind="theorem")],
                text="Fixture paper text.",
                input_sha256="a" * 64,
                extractor="fixture",
            ),
        )
        monkeypatch.setattr(
            "autolean.__main__._connected_llm",
            lambda *args, **kwargs: pytest.fail("model backend was acquired"),
        )

        result = runner.invoke(
            main,
            [
                "verify-paper",
                "fixture",
                "--extract-only",
                "--program",
                str(project_dir / "program.md"),
            ],
        )

        assert result.exit_code == 0
        assert "Theorem 1" in result.output

    def test_extract_only_materializes_text_without_model_claim_detection(
        self,
        runner: CliRunner,
        project_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from autolean.paper import PaperDocument

        reads = 0

        def read_once(source: str, pages: str | None = None, **kwargs: object) -> PaperDocument:
            nonlocal reads
            del source, pages, kwargs
            reads += 1
            return PaperDocument(
                title="Fixture",
                text="A" * 200,
                input_ref="fixture.pdf",
                input_sha256="a" * 64,
                extractor="hybrid",
            )

        monkeypatch.setattr("autolean.paper.read_paper", read_once)
        monkeypatch.setattr(
            "autolean.__main__._connected_llm",
            lambda *args, **kwargs: pytest.fail("model backend was acquired"),
        )

        result = runner.invoke(
            main,
            [
                "verify-paper",
                "fixture",
                "--extract-only",
                "--program",
                str(project_dir / "program.md"),
            ],
        )

        assert result.exit_code == 0
        assert reads == 1
        artifact = (
            project_dir
            / "workspace"
            / "AutoLean"
            / "Papers"
            / (f"Paper_{'a' * 12}_{hashlib.sha256(('A' * 200).encode()).hexdigest()[:12]}.md")
        )
        assert artifact.read_text(encoding="utf-8").endswith("A" * 200 + "\n")
        assert "Extracted paper artifact" in result.output

    def test_extraction_failure_is_a_nonzero_command_result(
        self,
        runner: CliRunner,
        project_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def fail(source: str, pages: str | None = None, **kwargs: object) -> object:
            del source, pages, kwargs
            raise OSError("fixture unavailable")

        monkeypatch.setattr("autolean.paper.read_paper", fail)
        result = runner.invoke(
            main,
            [
                "verify-paper",
                "fixture",
                "--extract-only",
                "--program",
                str(project_dir / "program.md"),
            ],
        )

        assert result.exit_code != 0
        assert "Paper extraction failed" in result.output


# ---------------------------------------------------------------------------
# Finetune config
# ---------------------------------------------------------------------------


class TestFinetuneConfigCommand:
    def test_generates_axolotl_config(self, runner: CliRunner, project_dir: Path) -> None:
        result = runner.invoke(
            main,
            [
                "finetune-config",
                "-d",
                str(project_dir / "workspace"),
                "--framework",
                "axolotl",
            ],
        )
        assert result.exit_code == 0
        assert "axolotl" in result.output.lower()
        config_file = project_dir / "workspace" / "training_data" / "axolotl_config.yaml"
        assert config_file.exists()

    def test_generates_trl_config(self, runner: CliRunner, project_dir: Path) -> None:
        result = runner.invoke(
            main,
            [
                "finetune-config",
                "-d",
                str(project_dir / "workspace"),
                "--framework",
                "trl",
            ],
        )
        assert result.exit_code == 0
        assert "dpo" in result.output.lower()


# ---------------------------------------------------------------------------
# Results command
# ---------------------------------------------------------------------------


class TestResultsCommand:
    def test_results_no_file(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(main, ["results", "-f", str(tmp_path / "noexist.tsv")])
        assert result.exit_code == 0
        assert "No results" in result.output

    def test_results_with_data(self, runner: CliRunner, tmp_path: Path) -> None:
        tsv = tmp_path / "results.tsv"
        tsv.write_text(
            "cycle\ttimestamp\ttarget_id\tdecl_name\tfile\tline\t"
            "outcome\tattempt\tduration_s\tllm_tokens\tllm_tok_s\t"
            "proof_lines\terror_category\tbuild_s\terror\n"
            "1\t2026-01-01\tt1\tfoo\tF.lean\t10\tsuccess\t1\t5.0\t100\t50.0\t1\t\t0.5\t\n"
        )
        result = runner.invoke(main, ["results", "-f", str(tsv)])
        assert result.exit_code == 0
        assert "foo" in result.output
        assert "success" in result.output
