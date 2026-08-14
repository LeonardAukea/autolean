"""Opt-in scripted walkthrough of the first-proof tutorial.

Drives the installed `autolean` binary through the documented flow —
models, init, doctor, prove, sessions, export — against a loopback
OpenAI-compatible server whose replies are keyed on request content.
The Lean side runs in the pinned Mathlib workspace, so every accepted
proof is kernel-checked and the walkthrough is deterministic.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("AUTOLEAN_RUN_TUTORIAL_E2E") != "1",
    reason="set AUTOLEAN_RUN_TUTORIAL_E2E=1 for the scripted tutorial walkthrough",
)

_STEP_TIMEOUT_SECONDS = 900
_MODEL = "tutorial-scripted"

#: A strategy honouring the exact field contract of `autolean.strategy`.
_PLAN: dict[str, object] = {
    "objective": "Prove that 1 + 1 = 2 in the natural numbers.",
    "formalization": ["State the equation over Nat with explicit numerals."],
    "observations": ["Both sides reduce to the numeral 2."],
    "invariants": ["Preserve the stated equation exactly."],
    "obstructions": ["Reject any term that changes the statement."],
    "reductions": ["Reduce both sides to normal form."],
    "premises": ["Use definitional unfolding of Nat addition."],
    "methods": ["Close the goal by reflexivity."],
    "partial_results": [],
    "risks": ["None; the statement is decidable by computation."],
    "completion_criteria": ["Lean accepts the theorem without placeholders."],
    "checkpoints": ["Elaborate the candidate in the sandbox."],
    "revision_triggers": ["A kernel diagnostic contradicts the method."],
}

# The name must be absent from Mathlib's root namespace: the isolated
# formalization compile imports Mathlib, which already declares
# `one_add_one_eq_two`.
_FORMALIZATION = "theorem tutorial_one_add_one_eq_two : (1 : Nat) + 1 = 2 := by\n  sorry"


def _scripted_reply(system: str, user: str) -> str:
    """Answer one chat request from its content alone.

    Content-keyed replies keep bounded repair and replan loops
    deterministic: the same question always receives the same answer,
    however many requests preceded it.
    """
    if "AutoLeanBackendSmoke" in user:
        return "trivial"
    if "mathematical research planner" in system:
        return json.dumps(_PLAN)
    if "formalization expert" in system:
        return _FORMALIZATION
    return "rfl"


class _ScriptedModelHandler(BaseHTTPRequestHandler):
    """The OpenAI-compatible surface consumed by the CLI's backend."""

    protocol_version = "HTTP/1.1"

    def _send_json(self, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path != "/v1/models":
            self.send_error(404)
            return
        self._send_json({"data": [{"id": _MODEL}]})

    def do_POST(self) -> None:
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length))
        messages = request["messages"]
        system = str(messages[0].get("content", ""))
        user = str(messages[1].get("content", ""))
        self._send_json(
            {
                "model": _MODEL,
                "choices": [{"message": {"content": _scripted_reply(system, user)}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }
        )

    def log_message(self, format: str, *args: object) -> None:
        del format, args


@pytest.fixture()
def project_root() -> Path:
    configured = os.environ.get("AUTOLEAN_TUTORIAL_PROJECT")
    root = Path(configured) if configured is not None else Path(__file__).resolve().parents[1] / "workspace"
    if not (root / ".lake" / "packages" / "mathlib").exists():
        pytest.skip("the tutorial walkthrough needs a Mathlib-provisioned workspace")
    return root


@pytest.fixture()
def scripted_model() -> Iterator[int]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ScriptedModelHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture()
def program_file(tmp_path: Path, project_root: Path, scripted_model: int) -> Path:
    program = tmp_path / "program.md"
    program.write_text(
        "# AutoLean Program\n"
        "\n"
        "## Mode\n"
        "\n"
        "sorry-elimination\n"
        "\n"
        "## Lean Project Path\n"
        "\n"
        f"{project_root}\n"
        "\n"
        "## LLM Configuration\n"
        "\n"
        f"model: {_MODEL}\n"
        "backend: openai_compat\n"
        f"endpoint: http://127.0.0.1:{scripted_model}\n"
        "temperature: 0.0\n"
        "max_retries_per_sorry: 2\n"
        "cycle_timeout_seconds: 240\n"
        "max_cycles: 3\n",
        encoding="utf-8",
    )
    return program


@pytest.fixture()
def committable_generated(project_root: Path) -> Iterator[None]:
    """Let the enclosing repository accept the agent's proof commit.

    The agent commits an accepted proof into the repository enclosing
    the Lean project, and `git add --intent-to-add` refuses ignored
    paths. The AutoLean checkout ignores `workspace/AutoLean/Generated/`
    as runtime state, so the walkthrough un-ignores it for the duration
    of the run through a deeper, higher-precedence ignore file.
    """
    marker = project_root / "AutoLean" / ".gitignore"
    if marker.exists():
        yield
        return
    marker.write_text("!Generated/\n", encoding="utf-8")
    try:
        yield
    finally:
        marker.unlink(missing_ok=True)


@pytest.mark.usefixtures("committable_generated")
def test_tutorial_first_proof_end_to_end(
    tmp_path: Path,
    project_root: Path,
    program_file: Path,
) -> None:
    """Walk docs/tutorials/first-proof.md against the scripted model."""
    autolean = shutil.which("autolean")
    assert autolean is not None, "the tutorial walkthrough needs the autolean binary on PATH"

    def run(*args: str, cwd: Path = tmp_path, stdin: str | None = None) -> str:
        result = subprocess.run(
            [autolean, *args],
            cwd=cwd,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=_STEP_TIMEOUT_SECONDS,
        )
        assert result.returncode == 0, (
            f"autolean {' '.join(args)} exited {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        return result.stdout

    # 1. Model profiles render without any authenticated provider.
    run("models")

    # 2. `init` scaffolds a pinned project beside a fresh program.md.
    scaffold = tmp_path / "first-proof"
    scaffold.mkdir()
    run("init", "lean", cwd=scaffold)
    lakefile = (scaffold / "lean" / "lakefile.lean").read_text(encoding="utf-8")
    assert "mathlib4" in lakefile
    assert "cslib" in lakefile
    assert (scaffold / "lean" / "lean-toolchain").read_text(encoding="utf-8").strip()
    assert (scaffold / "lean" / "lean.lean").is_file()
    assert (scaffold / "program.md").is_file()

    # 3. `doctor` checks the model and the sandboxed Lean path.
    doctor = run("doctor", "--program", str(program_file))
    assert "Model proof passed sandboxed Lean" in doctor
    assert "Build succeeded" in doctor

    # 4. `prove` plans, formalizes, proves, and accepts the statement.
    generated_dir = project_root / "AutoLean" / "Generated"
    before = set(generated_dir.glob("TutorialOneAddOneEqTwo*.lean"))
    prove = run(
        "prove",
        "1 + 1 = 2",
        "--review-plan",
        "--program",
        str(program_file),
        stdin="y\n",
    )
    assert "Mathematical research plan" in prove
    assert "Formalization compiled" in prove
    assert "Session complete" in prove
    created = set(generated_dir.glob("TutorialOneAddOneEqTwo*.lean")) - before
    assert len(created) == 1
    generated = created.pop()
    source = generated.read_text(encoding="utf-8")
    assert "theorem tutorial_one_add_one_eq_two" in source
    assert re.search(r"(?m)^[ \t]*sorry\b", source) is None

    # 5. The durable session binds the accepted proof.
    listing = json.loads(run("sessions", "--json", "--program", str(program_file)))
    matches = [item for item in listing if str(item["target_file"]).endswith(generated.name)]
    assert matches, f"no session records the accepted proof {generated.name}"
    record = matches[0]
    assert record["kind"] == "theorem"
    assert record["status"] == "completed"
    assert record["remaining_targets"] == 0

    # 6. `export` produces a standalone artifact with a manifest.
    artifact = tmp_path / "tutorial-artifact"
    run(
        "export",
        str(artifact),
        "--title",
        "A checked tutorial theorem",
        "--session",
        str(record["id"]),
        "--program",
        str(program_file),
    )
    manifest = json.loads((artifact / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["files"]
    assert all(re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) for entry in manifest["files"])
    assert (artifact / "project" / "lean-toolchain").is_file()
    assert (artifact / "project" / "lakefile.lean").is_file()
    assert (artifact / "project" / "AutoLean" / "Generated" / generated.name).is_file()
    assert (artifact / "paper" / "main.tex").is_file()
    assert (artifact / "README.md").is_file()
    assert (artifact / "session.json").is_file()

    # 7. The tutorial's own export command names no session. That form has to
    #    carry the proof too, and its project must elaborate on its own.
    whole = tmp_path / "tutorial-artifact-whole"
    run(
        "export",
        str(whole),
        "--title",
        "A checked tutorial theorem",
        "--program",
        str(program_file),
    )
    exported = whole / "project" / "AutoLean" / "Generated" / generated.name
    assert exported.is_file(), "a whole-project export must carry the accepted proof"
    assert "sorry" not in exported.read_text(encoding="utf-8")
    library_root = (whole / "project" / "AutoLean.lean").read_text(encoding="utf-8")
    assert f"import AutoLean.Generated.{generated.stem}" in library_root, (
        "the exported library root must build the proof it carries"
    )
    assert not (whole / "project" / "workspace").exists()

    # 8. The exported source elaborates under the pinned toolchain, which is
    #    what continuing outside this tool has to mean.
    lake = shutil.which("lake")
    assert lake is not None, "the tutorial walkthrough needs lake on PATH"
    elaboration = subprocess.run(
        [lake, "env", "lean", str(Path("AutoLean") / "Generated" / generated.name)],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=_STEP_TIMEOUT_SECONDS,
    )
    assert elaboration.returncode == 0, elaboration.stdout + elaboration.stderr
