from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from scripts.demo_media import (
    DEMO_HEIGHT,
    DEMO_MAX_SECONDS,
    DEMO_WIDTH,
    assert_tape_contract,
    playback_speed,
    preserve_workspace,
    publish_outputs,
    require_media_tools,
    staging_outputs,
    validate_media,
    vhs_version,
)

ROOT = Path(__file__).parents[1]


def test_missing_validator_fails_before_a_live_recording(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("scripts.demo_media.shutil.which", lambda name: None if name == "ffprobe" else name)
    with pytest.raises(SystemExit, match="ffprobe"):
        require_media_tools()


def test_failed_demo_retains_media_and_source_without_dependency_cache(tmp_path: Path) -> None:
    root = tmp_path / "work"
    root.mkdir()
    (root / "Proof.lean").write_text("theorem proof : True := by trivial\n")
    (root / ".lake").mkdir()
    (root / ".lake" / "large-cache").write_bytes(b"cache")

    with (
        pytest.raises(RuntimeError, match="validator failed"),
        preserve_workspace(tmp_path, root),
        staging_outputs(tmp_path, "demo") as staged,
    ):
        staged[0].write_bytes(b"gif")
        staged[1].write_bytes(b"mp4")
        raise RuntimeError("validator failed")

    artifacts = tmp_path / ".autolean" / "demo-diagnostics"
    assert next(artifacts.glob("*/Proof.lean")).read_bytes() == (root / "Proof.lean").read_bytes()
    assert next(artifacts.glob("*/demo.mp4")).read_bytes() == b"mp4"
    assert not list(artifacts.glob("*/.lake"))
    assert not list((tmp_path / "docs" / "assets").iterdir())


def _write_tape(repository: Path, body: str) -> Path:
    demos = repository / "docs" / "demos"
    demos.mkdir(parents=True)
    (demos / "style.tape").write_text(
        "Set Width 1280\nSet Height 800\nSet PlaybackSpeed 1.5\n",
        encoding="utf-8",
    )
    tape = demos / "example.tape"
    tape.write_text(body, encoding="utf-8")
    return tape


def test_tape_contract_binds_tape_and_shared_style(tmp_path: Path) -> None:
    tape = _write_tape(
        tmp_path,
        "Require autolean\nRequire lake\nSource docs/demos/style.tape\n",
    )

    sources = assert_tape_contract(tmp_path, tape, ("autolean", "lake"))

    assert [source["name"] for source in sources] == [
        "docs/demos/example.tape",
        "docs/demos/style.tape",
    ]
    assert (
        sources[1]["sha256"]
        == hashlib.sha256((tmp_path / "docs" / "demos" / "style.tape").read_bytes()).hexdigest()
    )
    assert playback_speed(tmp_path) == 1.5


def test_tape_contract_rejects_an_undeclared_command(tmp_path: Path) -> None:
    tape = _write_tape(
        tmp_path,
        "Require autolean\nSource docs/demos/style.tape\n",
    )

    with pytest.raises(SystemExit, match="lake"):
        assert_tape_contract(tmp_path, tape, ("autolean", "lake"))


def test_tape_contract_rejects_a_wait_armed_after_submission(tmp_path: Path) -> None:
    tape = _write_tape(
        tmp_path,
        "Require autolean\n"
        "Source docs/demos/style.tape\n"
        'Type "autolean prove"\n'
        "Enter\n"
        "Sleep 2s\n"
        "Hide\n"
        "Wait /complete/\n",
    )

    with pytest.raises(SystemExit, match="after submitting"):
        assert_tape_contract(tmp_path, tape, ("autolean",))


def test_media_is_staged_then_published_on_the_asset_filesystem(
    tmp_path: Path,
) -> None:
    with staging_outputs(tmp_path, "demo") as staged:
        assert all(path.parent.parent == tmp_path / "docs" / "assets" for path in staged)
        staged[0].write_bytes(b"gif")
        staged[1].write_bytes(b"mp4")
        published = publish_outputs(tmp_path, staged)

    assert [path.name for path in published] == ["demo.gif", "demo.mp4"]
    assert [path.read_bytes() for path in published] == [b"gif", b"mp4"]


def test_vhs_version_uses_the_resolved_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def run(command: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        observed["command"] = command
        observed.update(options)
        return subprocess.CompletedProcess(command, 0, "vhs version 0.11.0\n", "")

    monkeypatch.setattr("scripts.demo_media.shutil.which", lambda _name: "/nix/vhs")
    monkeypatch.setattr("scripts.demo_media.subprocess.run", run)

    assert vhs_version() == "0.11.0"
    assert observed["command"] == ["/nix/vhs", "--version"]
    assert observed["timeout"] == 30


def test_rendered_media_records_geometry_and_duration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media = [tmp_path / "demo.gif", tmp_path / "demo.mp4"]
    for path in media:
        path.write_bytes(path.suffix.encode())

    def run(command: list[str], **_options: object) -> subprocess.CompletedProcess[str]:
        payload = {
            "streams": [{"avg_frame_rate": "25/1", "height": DEMO_HEIGHT, "width": 1280}],
            "format": {"duration": "61.234000"},
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr("scripts.demo_media.shutil.which", lambda _name: "/nix/ffprobe")
    monkeypatch.setattr("scripts.demo_media.subprocess.run", run)

    records = validate_media(media)

    assert records[0] == {
        "duration_seconds": 61.234,
        "frame_rate": "25/1",
        "height": DEMO_HEIGHT,
        "name": "demo.gif",
        "sha256": hashlib.sha256(b".gif").hexdigest(),
        "size": 4,
        "width": 1280,
    }


@pytest.mark.parametrize(
    ("width", "height", "duration", "message"),
    [
        (1280, 720, "60.0", "geometry"),
        (DEMO_WIDTH, DEMO_HEIGHT, "90.1", "duration"),
        (DEMO_WIDTH, DEMO_HEIGHT, "NaN", "duration"),
        (DEMO_WIDTH, DEMO_HEIGHT, "Infinity", "duration"),
    ],
)
def test_rendered_media_rejects_an_invalid_presentation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    width: int,
    height: int,
    duration: str,
    message: str,
) -> None:
    path = tmp_path / "demo.mp4"
    path.write_bytes(b"video")

    def run(command: list[str], **_options: object) -> subprocess.CompletedProcess[str]:
        payload = {
            "streams": [{"avg_frame_rate": "25/1", "height": height, "width": width}],
            "format": {"duration": duration},
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr("scripts.demo_media.shutil.which", lambda _name: "/nix/ffprobe")
    monkeypatch.setattr("scripts.demo_media.subprocess.run", run)

    with pytest.raises(SystemExit, match=message):
        validate_media([path])


@pytest.mark.parametrize("name", ["pythagorean", "ionescu-tulcea", "gromov"])
def test_checked_in_demo_manifest_matches_its_sources(name: str) -> None:
    record = json.loads((ROOT / "docs" / "demos" / f"{name}.json").read_text())

    sources = record["tape_sources"]
    if name != "gromov":
        assert record["tape_sha256"] == sources[0]["sha256"]
    for source in sources:
        path = ROOT / source["name"]
        assert path.stat().st_size == source["size"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == source["sha256"]

    durations: list[float] = []
    for medium in record["media"]:
        path = ROOT / "docs" / "assets" / medium["name"]
        assert path.stat().st_size == medium["size"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == medium["sha256"]
        assert (medium["width"], medium["height"]) == (DEMO_WIDTH, DEMO_HEIGHT)
        duration = float(medium["duration_seconds"])
        assert 0 < duration <= DEMO_MAX_SECONDS
        durations.append(duration)
    assert max(durations) - min(durations) <= 1.0
    assert record["vhs_version"]


def test_gromov_recording_binds_accepted_learning_and_the_open_boundary() -> None:
    from autolean.progress import PROGRESS_PREFIX, ProgressEvent, ProgressKind
    from autolean.scanner import scan_file

    directory = ROOT / "docs" / "demos"
    record = json.loads((directory / "gromov.json").read_text())
    assert record["schema"] == "autolean.research-demo.v1"
    assert (record["model"], record["effort"]) == ("gpt-6-astra", "max")
    for field in ("source_after", "activity"):
        identity = record[field]
        payload = (directory / identity["name"]).read_bytes()
        assert len(payload) == identity["size"]
        assert hashlib.sha256(payload).hexdigest() == identity["sha256"]
    template = (ROOT / "autolean" / "examples" / "gromov.lean").read_bytes()
    assert hashlib.sha256(template).hexdigest() == record["source_before_sha256"]
    source = directory / record["source_after"]["name"]
    assert sorted(target.decl_name for target in scan_file(source)) == record["open_targets"]
    support = record["accepted_support"]
    assert support["outcome"] == "success"
    assert support["model"] == record["model"]
    assert support["source_after_sha256"] == record["source_after"]["sha256"]
    assert set(support["axioms"].split(",")) <= {"Classical.choice", "Quot.sound", "propext"}
    assert support["decl_name"] not in record["open_targets"]
    assert record["open_target"] in record["open_targets"]
    activity = directory / record["activity"]["name"]
    events = [ProgressEvent.from_line(PROGRESS_PREFIX + line) for line in activity.read_text().splitlines()]
    assert all(event is not None for event in events)
    open_events = [event for event in events if event.target == record["open_target"]]
    assert any(event.kind is ProgressKind.LEARNING for event in open_events)
    assert any(event.kind is ProgressKind.FEEDBACK for event in open_events)
    assert not any(
        event.kind is ProgressKind.FEEDBACK and event.message == "success" for event in open_events
    )
    assert open_events[-1].kind is ProgressKind.FINISHED
