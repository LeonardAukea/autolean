"""Optional research tools report readiness without changing core health."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError

import pytest

from autolean.research_tools import research_tools


def test_pdf_runtime_is_an_optional_capability(monkeypatch: pytest.MonkeyPatch) -> None:
    class Search:
        def identity(self) -> str:
            return "CodeDB fixture"

    def missing(_: str) -> str:
        raise PackageNotFoundError

    monkeypatch.setattr("autolean.research_tools.version", missing)
    monkeypatch.setattr("autolean.research_tools.lightpanda_identity", lambda: "Lightpanda fixture")
    monkeypatch.setattr("autolean.research_tools.CodeDBSearchProvider", Search)

    pdf = research_tools()[0]

    assert pdf.name == "PDF extraction"
    assert not pdf.available
    assert not pdf.required
