"""Validate the shared VHS demo contract and rendered media."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path

DEMO_STYLE = Path("docs/demos/style.tape")
DEMO_WIDTH = 1280
DEMO_HEIGHT = 800
DEMO_MAX_SECONDS = 90.0


def _diagnostics_directory(repository: Path) -> Path:
    path = repository / ".autolean" / "demo-diagnostics"
    path.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def preserve_workspace(repository: Path, root: Path) -> Iterator[None]:
    """Retain failed source and records through temporary workspace cleanup."""
    try:
        yield
    except BaseException:
        destination = Path(tempfile.mkdtemp(prefix=root.name + "-", dir=_diagnostics_directory(repository)))
        shutil.copytree(root, destination, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".lake", ".git"))
        print(f"Failed demo workspace: {destination}", flush=True)
        raise


def require_media_tools() -> None:
    """Check the complete render and validation toolchain before a live run."""
    missing = [name for name in ("vhs", "ffmpeg", "ffprobe") if shutil.which(name) is None]
    if missing:
        raise SystemExit(f"recording tools unavailable: {', '.join(missing)}; enter `nix develop`")


def render_tape(
    repository: Path,
    tape: Path,
    outputs: Sequence[Path],
    *,
    environment: Mapping[str, str],
    timeout: int,
) -> None:
    """Render media and retain a terminal transcript for failure diagnosis."""
    with tempfile.NamedTemporaryFile(
        prefix=tape.stem + "-", suffix=".txt", dir=_diagnostics_directory(repository), delete=False
    ) as file:
        transcript = Path(file.name)
    command = ["vhs"]
    for path in (*outputs, transcript):
        command.extend(("--output", str(path)))
    command.append(str(tape.relative_to(repository)))
    try:
        subprocess.run(command, cwd=repository, env=environment, check=True, timeout=timeout)
    finally:
        print(f"Terminal transcript: {transcript}", flush=True)


def sha256(path: Path) -> str:
    """Return the SHA-256 identity of one file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def file_identity(path: Path, *, name: str | None = None) -> dict[str, object]:
    """Describe a file by its stable content identity."""
    return {
        "name": name or path.name,
        "sha256": sha256(path),
        "size": path.stat().st_size,
    }


def completed_session(root: Path, kind: str) -> dict[str, object]:
    """Return the one completed session of `kind` in the demo workspace."""
    sessions = (root / "workspace" / ".autolean" / "sessions").glob("*.json")
    records = [json.loads(path.read_text(encoding="utf-8")) for path in sessions]
    completed = [
        record
        for record in records
        if record.get("schema") == "autolean.proof-session.v1"
        and record.get("kind") == kind
        and record.get("status") == "completed"
    ]
    if len(completed) != 1 or completed[0].get("remaining_targets") != 0:
        raise SystemExit(f"demo {kind} session is not complete")
    return completed[0]


@contextmanager
def staging_outputs(repository: Path, stem: str) -> Iterator[list[Path]]:
    """Yield same-filesystem output paths for one VHS render."""
    assets = repository / "docs" / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".autolean-demo-", dir=assets) as root:
        directory = Path(root)
        try:
            yield [directory / f"{stem}.gif", directory / f"{stem}.mp4"]
        except BaseException:
            destination = _diagnostics_directory(repository) / directory.name
            shutil.move(str(directory), destination)
            print(f"Failed demo media: {destination}", flush=True)
            raise


def publish_outputs(repository: Path, staged: Sequence[Path]) -> list[Path]:
    """Promote validated media to the public asset paths."""
    assets = repository / "docs" / "assets"
    published: list[Path] = []
    for source in staged:
        destination = assets / source.name
        source.replace(destination)
        published.append(destination)
    return published


def _settings(path: Path) -> dict[str, str]:
    settings: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("Set "):
            continue
        _, name, value = line.split(maxsplit=2)
        settings[name] = value.strip('"')
    return settings


def assert_tape_contract(
    repository: Path,
    tape: Path,
    required_commands: Sequence[str],
) -> list[dict[str, object]]:
    """Validate and identify the source files that control one recording."""
    style = repository / DEMO_STYLE
    if not style.is_file():
        raise SystemExit(f"demo style is unavailable: {style}")

    lines = tape.read_text(encoding="utf-8").splitlines()
    source = f"Source {DEMO_STYLE.as_posix()}"
    if lines.count(source) != 1:
        raise SystemExit(f"demo tape must contain exactly one `{source}` directive")

    requirements = {line.removeprefix("Require ").strip() for line in lines if line.startswith("Require ")}
    missing = sorted(set(required_commands) - requirements)
    if missing:
        raise SystemExit(f"demo tape omits required commands: {', '.join(missing)}")

    hidden = False
    last_hide = -1
    last_enter = -1
    for index, line in enumerate(lines):
        if line == "Hide":
            hidden = True
            last_hide = index
        elif line == "Show":
            hidden = False
        elif line.startswith("Enter"):
            last_enter = index
        elif line.startswith("Wait") and hidden and last_hide > last_enter:
            raise SystemExit("demo tape arms a hidden wait after submitting its command")

    settings = _settings(style)
    expected = {"Width": str(DEMO_WIDTH), "Height": str(DEMO_HEIGHT)}
    mismatches = {
        name: (settings.get(name), value) for name, value in expected.items() if settings.get(name) != value
    }
    if mismatches:
        raise SystemExit(f"demo style has unsupported media geometry: {mismatches}")

    return [
        file_identity(tape, name=str(tape.relative_to(repository))),
        file_identity(style, name=DEMO_STYLE.as_posix()),
    ]


def playback_speed(repository: Path) -> float:
    """Return the playback speed declared by the shared demo style."""
    value = _settings(repository / DEMO_STYLE).get("PlaybackSpeed", "1.0")
    speed = float(value)
    if not math.isfinite(speed) or speed <= 0:
        raise SystemExit("demo playback speed must be finite and positive")
    return speed


def vhs_version() -> str:
    """Return the VHS version responsible for a recording."""
    executable = shutil.which("vhs")
    if executable is None:
        raise SystemExit("vhs is unavailable; enter `nix develop`")
    result = subprocess.run(
        [executable, "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    prefix = "vhs version "
    version = result.stdout.strip()
    if not version.startswith(prefix):
        raise SystemExit(f"vhs returned an unsupported version string: {version!r}")
    return version.removeprefix(prefix)


def _probe_media(path: Path) -> dict[str, object]:
    executable = shutil.which("ffprobe")
    if executable is None:
        raise SystemExit("ffprobe is unavailable; enter `nix develop`")
    result = subprocess.run(
        [
            executable,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,avg_frame_rate:format=duration",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    payload = json.loads(result.stdout)
    streams = payload.get("streams")
    media_format = payload.get("format")
    if not isinstance(streams, list) or len(streams) != 1:
        raise SystemExit(f"recording has no single video stream: {path}")
    if not isinstance(media_format, dict):
        raise SystemExit(f"recording has no media format metadata: {path}")
    stream = streams[0]
    if not isinstance(stream, dict):
        raise SystemExit(f"recording has invalid video metadata: {path}")
    try:
        duration = round(float(media_format["duration"]), 3)
        width = int(stream["width"])
        height = int(stream["height"])
        frame_rate = str(stream["avg_frame_rate"])
    except (KeyError, TypeError, ValueError) as error:
        raise SystemExit(f"recording has incomplete video metadata: {path}") from error
    return {
        **file_identity(path),
        "duration_seconds": duration,
        "frame_rate": frame_rate,
        "height": height,
        "width": width,
    }


def validate_media(paths: Sequence[Path]) -> list[dict[str, object]]:
    """Validate the geometry and duration of one demo's rendered outputs."""
    records: list[dict[str, object]] = []
    for path in paths:
        if not path.is_file() or path.stat().st_size == 0:
            raise SystemExit(f"recording did not produce {path}")
        record = _probe_media(path)
        geometry = (record["width"], record["height"])
        if geometry != (DEMO_WIDTH, DEMO_HEIGHT):
            raise SystemExit(f"recording has unsupported geometry {geometry}: {path}")
        duration = float(record["duration_seconds"])
        if not math.isfinite(duration) or duration <= 0 or duration > DEMO_MAX_SECONDS:
            raise SystemExit(
                f"recording duration {duration:.3f}s is outside (0, {DEMO_MAX_SECONDS:.0f}] seconds: {path}"
            )
        records.append(record)

    durations = [float(record["duration_seconds"]) for record in records]
    if durations and max(durations) - min(durations) > 1.0:
        raise SystemExit(f"recording output durations differ: {durations}")
    return records
