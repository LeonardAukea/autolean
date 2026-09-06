"""Real child processes exercise output bounds, drainage, and ownership."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from autolean.process import ProcessOutputError, run_process


def test_large_bidirectional_transfer_and_nonzero_status() -> None:
    source = (
        "import sys; sys.stderr.write('e' * 200000); sys.stderr.flush(); "
        "data=sys.stdin.read(); sys.stdout.write(data); sys.exit(7)"
    )
    text = "∀x. " * 100000
    result = run_process([sys.executable, "-c", source], input=text, timeout=10)
    assert result.returncode == 7
    assert result.stdout == text
    assert result.stderr == "e" * 200000


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_output_budget_is_enforced_during_capture(stream: str) -> None:
    source = f"import sys; stream=sys.{stream}; stream.write('x' * 1000000); stream.flush()"
    with pytest.raises(ProcessOutputError, match="1024-byte limit"):
        run_process([sys.executable, "-c", source], timeout=10, max_output_bytes=1024)


def test_stdout_and_stderr_share_one_budget() -> None:
    with pytest.raises(ProcessOutputError):
        run_process(
            [sys.executable, "-c", "import os; os.write(1, b'a' * 600); os.write(2, b'b' * 600)"],
            timeout=10,
            max_output_bytes=1000,
        )


def test_exact_output_budget_and_early_stdin_close() -> None:
    result = run_process(
        [sys.executable, "-c", "import os; os.close(0); os.write(1, b'a' * 1024)"],
        input="x" * 1000000,
        timeout=10,
        max_output_bytes=1024,
    )
    assert result.stdout == "a" * 1024
    assert result.returncode == 0


def test_invalid_output_encoding_fails_closed() -> None:
    with pytest.raises(ProcessOutputError, match="UTF-8"):
        run_process([sys.executable, "-c", "import os; os.write(1, b'\\xff')"], timeout=10)


@pytest.mark.parametrize("parent_exits", [False, True])
def test_timeout_owns_descendants_and_inherited_pipes(tmp_path: Path, parent_exits: bool) -> None:
    sentinel = tmp_path / "escaped"
    child = "import pathlib,time; time.sleep(1); pathlib.Path('escaped').write_text('alive')"
    source = f"import subprocess,sys,time; subprocess.Popen([sys.executable, '-c', {child!r}]); " + (
        "sys.exit(0)" if parent_exits else "time.sleep(10)"
    )
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run_process([sys.executable, "-c", source], cwd=tmp_path, timeout=0.2)
    assert time.monotonic() - start < 3
    time.sleep(1)
    assert not sentinel.exists()


def test_closed_output_pipes_do_not_bypass_the_deadline() -> None:
    with pytest.raises(subprocess.TimeoutExpired):
        run_process(
            [sys.executable, "-c", "import os,time; os.close(1); os.close(2); time.sleep(10)"],
            timeout=0.2,
        )


def test_repeated_runs_release_descriptors() -> None:
    directory = Path("/dev/fd") if sys.platform == "darwin" else Path("/proc/self/fd")
    before = len(os.listdir(directory))
    for _ in range(12):
        run_process([sys.executable, "-c", "print('ok')"], timeout=10)
    assert len(os.listdir(directory)) == before
