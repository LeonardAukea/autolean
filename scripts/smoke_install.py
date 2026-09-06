"""Exercise an installed distribution outside the source checkout."""

from __future__ import annotations

import importlib
import json
import pkgutil
import subprocess
import sys
import tempfile
from pathlib import Path

import autolean
from autolean.models import resolve_llm_config
from autolean.program import parse_program


def main() -> None:
    checkout = Path(__file__).resolve().parents[1]
    installed = Path(autolean.__file__).resolve()
    if installed.is_relative_to(checkout):
        raise SystemExit(f"smoke test requires an installed distribution: {installed}")
    modules = list(pkgutil.walk_packages(autolean.__path__, prefix="autolean."))
    for module in modules:
        importlib.import_module(module.name)
    config = resolve_llm_config("codex")
    assert (config.model, config.effort) == ("gpt-6-astra", "max")
    with tempfile.TemporaryDirectory(prefix="autolean-install-smoke-") as directory:
        for arguments in (("--version",), ("models", "codex", "--json"), ("init", "lean")):
            result = subprocess.run(
                [sys.executable, "-m", "autolean", *arguments],
                cwd=directory,
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
            if "--json" in arguments:
                assert json.loads(result.stdout)["schema"] == "autolean-model-catalog-v1"
        assert (Path(directory) / "lean" / "lean-toolchain").is_file()
        assert (Path(directory) / "program.md").is_file()
        program = parse_program(Path(directory) / "program.md")
        program.model = "codex"
        assert program.llm_config().model == "gpt-6-astra"
        subprocess.run(
            [sys.executable, "-m", "autolean", "init", "research", "--example", "gromov", "--no-cslib"],
            cwd=directory,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        research = (Path(directory) / "research" / "research.lean").read_text(encoding="utf-8")
        assert "theorem residual_finiteness_question" in research
        assert "∃ S : Finset G" in research
    print(f"Installed {len(modules)} modules: imports, model selection, CLI, and project creation passed")


if __name__ == "__main__":
    main()
