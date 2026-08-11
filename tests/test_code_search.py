from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from autolean.code_search import CodeDBSearchProvider


def test_codedb_search_is_bounded_local_and_advisory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "codedb"
    executable.write_text("fixture", encoding="utf-8")
    executable.chmod(0o700)
    calls: list[tuple[list[str], dict[str, object]]] = []

    def run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((arguments, kwargs))
        if arguments[-1] == "--version":
            return subprocess.CompletedProcess(arguments, 0, "codedb 0.2.5838\n", "")
        query = arguments[-1]
        return subprocess.CompletedProcess(
            arguments,
            0,
            f"AutoLean/Local.lean:3 theorem {query}_helper\n",
            "",
        )

    monkeypatch.setattr("autolean.code_search.subprocess.run", run)
    provider = CodeDBSearchProvider(str(executable))

    context = provider.search(tmp_path, "⊢ LocalThing n = n", "target_theorem")

    assert context.tool == "codedb 0.2.5838"
    assert context.queries == ("target_theorem", "LocalThing")
    assert "AutoLean/Local.lean" in context.render()
    assert "Lean elaboration determines" in context.render()
    search_calls = calls[1:]
    assert all(call[1]["cwd"] == tmp_path for call in search_calls)
    for _, arguments in search_calls:
        environment = arguments["env"]
        assert isinstance(environment, dict)
        assert environment["CODEDB_NO_TELEMETRY"] == "1"
        assert "OPENAI_API_KEY" not in environment


def test_missing_codedb_has_an_explicit_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("autolean.code_search.shutil.which", lambda command: None)

    context = CodeDBSearchProvider().search(Path.cwd(), "⊢ True", "target")

    assert context.unavailable_reason == "executable not found"
    assert "codedb/unavailable" in context.render()
