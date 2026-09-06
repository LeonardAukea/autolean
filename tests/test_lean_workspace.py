"""Smoke fixtures preserve the source checkout and its dependency artifacts."""

from pathlib import Path

from scripts.lean_workspace import clear_runtime_state, copy_workspace, initialize_repository


def test_smoke_workspace_owns_source_history_and_build_artifacts(tmp_path: Path) -> None:
    source = tmp_path / "original"
    for relative in ("Proof.lean", ".lake/build/Proof.olean", ".autolean/session.json", ".git/config"):
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative)
    before = {path.relative_to(source): path.read_bytes() for path in source.rglob("*") if path.is_file()}
    destination = tmp_path / "isolated"

    copy_workspace(source, destination)
    clear_runtime_state(destination)
    initialize_repository(destination)
    (destination / "Proof.lean").write_text("new proof")
    (destination / ".lake/build/Proof.olean").write_text("new artifact")

    assert not (destination / ".autolean/session.json").exists()
    assert (destination / ".git/HEAD").is_file()
    after = {path.relative_to(source): path.read_bytes() for path in source.rglob("*") if path.is_file()}
    assert after == before
