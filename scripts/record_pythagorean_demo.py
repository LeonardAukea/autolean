#!/usr/bin/env python3
"""Record the README Pythagorean theorem demo with real Lean validation."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MODEL_NAME = "autolean-demo"
MAX_REQUEST_BYTES = 2 * 1024 * 1024

PLAN = {
    "objective": ("Prove norm-square additivity for orthogonal vectors in a real inner product space."),
    "formalization": ["Quantify over a real inner product space and vectors x and y."],
    "observations": ["Orthogonality removes the cross term in the squared norm."],
    "invariants": [],
    "obstructions": ["Confirm the inner-product notation scope and real scalar field."],
    "reductions": ["Reduce the goal to Mathlib's orthogonal-vector norm identity."],
    "premises": ["Verify norm_add_sq_eq_norm_sq_add_norm_sq_real in the pinned Mathlib."],
    "methods": ["Apply the library theorem and normalize multiplication to powers."],
    "partial_results": [],
    "risks": [],
    "completion_criteria": ["Lean accepts the exact declaration without sorry or extra axioms."],
    "checkpoints": ["Compile the scaffold, validate the proof, then audit its axioms."],
    "revision_triggers": [],
}

FORMALIZATION = """\
open scoped InnerProductSpace

/-- Pythagorean theorem for orthogonal vectors in a real inner product space. -/
theorem pythagorean_theorem
    {V : Type*} [NormedAddCommGroup V] [InnerProductSpace ℝ V]
    (x y : V) (h : ⟪x, y⟫_ℝ = 0) :
    ‖x + y‖ ^ 2 = ‖x‖ ^ 2 + ‖y‖ ^ 2 := by
  sorry
"""

TYPE_MISMATCH_PROOF = "exact h"
PROOF = "simpa [pow_two] using norm_add_sq_eq_norm_sq_add_norm_sq_real h"


class DemoModelServer(ThreadingHTTPServer):
    """Local OpenAI-compatible server for a reproducible terminal demo."""

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
    ) -> None:
        self._proof_lock = threading.Lock()
        self._proof_requests = 0
        super().__init__(server_address, handler_class)

    def completion_for(self, system: str) -> str:
        """Return the next deterministic response for one model boundary."""
        if "mathematical research planner" in system:
            return json.dumps(PLAN, ensure_ascii=False)
        if "formalization expert" in system:
            return FORMALIZATION
        with self._proof_lock:
            self._proof_requests += 1
            if self._proof_requests == 1:
                return TYPE_MISMATCH_PROOF
        return PROOF


class DemoModelHandler(BaseHTTPRequestHandler):
    """Serve the three bounded completions used by the demo."""

    server: DemoModelServer

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def _write_json(self, status: int, payload: object) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path != "/v1/models":
            self._write_json(404, {"error": "not found"})
            return
        self._write_json(200, {"object": "list", "data": [{"id": MODEL_NAME}]})

    def do_POST(self) -> None:
        if self.path != "/v1/chat/completions":
            self._write_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_REQUEST_BYTES:
                raise ValueError("invalid request size")
            payload = json.loads(self.rfile.read(length))
            system = _system_message(payload)
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            self._write_json(400, {"error": str(error)})
            return

        text = self.server.completion_for(system)
        self._write_json(
            200,
            {
                "id": "autolean-demo",
                "object": "chat.completion",
                "created": 0,
                "model": MODEL_NAME,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": text},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": max(1, len(system) // 4),
                    "completion_tokens": max(1, len(text) // 4),
                    "total_tokens": max(2, (len(system) + len(text)) // 4),
                },
            },
        )


def _system_message(payload: object) -> str:
    if not isinstance(payload, dict):
        raise ValueError("request must be a JSON object")
    messages = payload.get("messages")
    if not isinstance(messages, list):
        raise ValueError("messages must be an array")
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "system":
            continue
        content = message.get("content")
        if isinstance(content, str) and content:
            return content
    raise ValueError("request has no system message")


@contextmanager
def demo_model_server(enabled: bool) -> Iterator[str | None]:
    """Serve deterministic completions when the recording is not live."""
    if not enabled:
        yield None
        return
    server = DemoModelServer(("127.0.0.1", 0), DemoModelHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _copy_workspace(source: Path, destination: Path) -> None:
    """Create an independent copy-on-write workspace for the recording."""
    if sys.platform == "darwin":
        command = ["/bin/cp", "-cR", str(source), str(destination)]
    else:
        command = ["cp", "--archive", "--reflink=auto", str(source), str(destination)]
    subprocess.run(command, check=True, capture_output=True, text=True)


def _remove_runtime_state(workspace: Path) -> None:
    """Keep local research state outside the recorded project."""
    for relative in (
        ".autolean",
        ".codedb",
        "logs",
        "skills",
        "training_data",
        "workspace",
        "AutoLean/Generated",
    ):
        shutil.rmtree(workspace / relative, ignore_errors=True)
    for relative in (
        ".overnight.pid",
        "overnight.log",
        "results.tsv",
        "AutoLean/UserTheorems.lean",
    ):
        (workspace / relative).unlink(missing_ok=True)


def _initialize_repository(workspace: Path) -> None:
    """Prepare the isolated project for AutoLean's exact proof commit."""
    (workspace / ".gitignore").write_text(
        ".autolean/\n"
        ".codedb/\n"
        ".lake/\n"
        "logs/\n"
        "skills/\n"
        "training_data/\n"
        "workspace/\n"
        "AutoLean/UserTheorems.lean\n"
        "results.tsv\n"
        "overnight.log\n",
        encoding="utf-8",
    )
    commands = (
        ("init", "-q"),
        ("config", "user.name", "AutoLean Demo"),
        ("config", "user.email", "demo@autolean.invalid"),
        ("config", "commit.gpgsign", "false"),
        ("add", "."),
        ("commit", "-qm", "demo: Record initial project"),
    )
    for arguments in commands:
        subprocess.run(
            ["git", "-c", "core.fsmonitor=false", *arguments],
            cwd=workspace,
            check=True,
        )


def _write_program(path: Path, endpoint: str | None, source_program: Path) -> None:
    """Write the model configuration used by one isolated recording."""
    if endpoint is None:
        shutil.copy2(source_program, path)
        return
    path.write_text(
        "# AutoLean demo\n\n"
        "## Mode\n\nsorry-elimination\n\n"
        "## Lean Project Path\n\nworkspace\n\n"
        "## LLM Configuration\n\n"
        f"model: {MODEL_NAME}\n"
        "backend: openai_compat\n"
        f"endpoint: {endpoint}\n"
        "temperature: 0\n"
        "max_output_tokens: 4096\n"
        "max_retries_per_sorry: 3\n"
        "cycle_timeout_seconds: 120\n"
        "llm_timeout_seconds: 120\n"
        "max_proof_lines: 12\n\n"
        "## Experiment Budget\n\nmax_cycles: 3\n",
        encoding="utf-8",
    )


@contextmanager
def demo_workspace(repository: Path, endpoint: str | None) -> Iterator[Path]:
    """Yield an isolated project with the repository's pinned Lean closure."""
    with tempfile.TemporaryDirectory(prefix="autolean-demo-") as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        _copy_workspace(repository / "workspace", workspace)
        _remove_runtime_state(workspace)
        _initialize_repository(workspace)
        _write_program(root / "program.md", endpoint, repository / "program.md")
        yield root


def _autolean() -> str:
    executable = shutil.which("autolean")
    if executable is None:
        raise SystemExit("autolean is unavailable; run this inside `nix develop`")
    return executable


def _check_demo(root: Path) -> None:
    """Run the exact proof workflow and require its accepted source."""
    command = [
        _autolean(),
        "prove",
        "the Pythagorean theorem",
        "--review-plan",
        "--max-attempts",
        "3",
    ]
    result = subprocess.run(
        command,
        cwd=root,
        input="y\n",
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        raise SystemExit(result.returncode)
    _assert_demo(root)


def _assert_proof(root: Path) -> None:
    """Require the exact accepted source produced by the demo."""
    generated = root / "workspace" / "AutoLean" / "Generated" / "PythagoreanTheorem.lean"
    source = generated.read_text(encoding="utf-8")
    if "simpa [pow_two]" not in source or re.search(r"(?m)^[ \t]*sorry\b", source):
        raise SystemExit("demo did not install the accepted Pythagorean proof")


def _assert_learning_trace(root: Path) -> None:
    """Require one rejected attempt, one correction, and one learned skill."""
    results = root / "workspace" / "results.tsv"
    with results.open(newline="", encoding="utf-8") as handle:
        rows = [
            row for row in csv.DictReader(handle, delimiter="\t") if row["decl_name"] == "pythagorean_theorem"
        ]
    attempts = [(row["attempt"], row["outcome"]) for row in rows]
    if attempts != [("1", "fail_build"), ("2", "success")]:
        raise SystemExit(f"demo produced an unexpected experiment trace: {attempts}")

    skill_path = root / "workspace" / "skills" / "single_simpa.json"
    skill = json.loads(skill_path.read_text(encoding="utf-8"))
    if skill.get("name") != "single_simpa" or skill.get("times_succeeded") != 1:
        raise SystemExit("demo did not learn the expected simpa skill")


def _assert_demo(root: Path) -> None:
    """Require the proof and the visible experiment-learning trace."""
    _assert_proof(root)
    _assert_learning_trace(root)


def _record_demo(repository: Path, root: Path) -> None:
    """Render the GIF and MP4 from the versioned VHS tape."""
    if shutil.which("vhs") is None:
        raise SystemExit("vhs is unavailable; run this inside `nix develop`")
    (repository / "docs" / "assets").mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["AUTOLEAN_DEMO_ROOT"] = str(root)
    subprocess.run(
        ["vhs", "docs/demos/pythagorean.tape"],
        cwd=repository,
        env=environment,
        check=True,
        timeout=600,
    )
    _assert_demo(root)
    for name in ("autolean-pythagorean.gif", "autolean-pythagorean.mp4"):
        path = repository / "docs" / "assets" / name
        if not path.is_file() or path.stat().st_size == 0:
            raise SystemExit(f"recording did not produce {path}")
        print(f"{path.relative_to(repository)} ({path.stat().st_size:,} bytes)")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="run the proof workflow without rendering media",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="use the model configured in program.md",
    )
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    """Prepare an isolated project and run or record the demo."""
    options = _parser().parse_args(arguments)
    repository = Path(__file__).resolve().parents[1]
    with demo_model_server(not options.live) as endpoint, demo_workspace(repository, endpoint) as root:
        if options.check:
            _check_demo(root)
        else:
            _record_demo(repository, root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
