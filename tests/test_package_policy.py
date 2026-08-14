"""Distribution metadata states package ownership and optional runtimes."""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_runtime_core_excludes_copyleft_pdf_stack() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = metadata["project"]

    assert project["name"] == "autolean-proof"
    assert metadata["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == ["autolean"]
    assert all(not requirement.startswith("pymupdf") for requirement in project["dependencies"])
    assert project["optional-dependencies"]["pdf"] == [
        "pymupdf>=1.28.2",
        "pymupdf4llm>=1.28.2",
    ]
