"""Bounded capture and process ownership for external command execution."""

from __future__ import annotations

import os
import selectors
import signal
import subprocess
import time
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path

from autolean.validation import require_int, require_number

MAX_OUTPUT_BYTES = 32 * 1024 * 1024
_CHUNK_BYTES = 64 * 1024


class ProcessOutputError(subprocess.SubprocessError):
    """Captured output exceeds its byte budget or contains invalid UTF-8."""


def _write_available(descriptor: int, pending: memoryview) -> memoryview:
    """Advance one nonblocking input chunk, retaining the unwritten suffix."""
    try:
        return pending[os.write(descriptor, pending[:_CHUNK_BYTES]) :]
    except BrokenPipeError:
        return pending[len(pending) :]
    except BlockingIOError:
        return pending


def _register_pipes(
    selector: selectors.BaseSelector, process: subprocess.Popen[bytes], pending: memoryview
) -> None:
    """Register each owned pipe for the direction in which it can progress."""
    for index, pipe in enumerate((process.stdout, process.stderr)):
        assert pipe is not None
        os.set_blocking(pipe.fileno(), False)
        selector.register(pipe, selectors.EVENT_READ, index)
    if process.stdin is None:
        return
    if not pending:
        process.stdin.close()
        return
    os.set_blocking(process.stdin.fileno(), False)
    selector.register(process.stdin, selectors.EVENT_WRITE, None)


def run_process(
    args: Sequence[str],
    *,
    timeout: float,
    input: str | None = None,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    max_output_bytes: int = MAX_OUTPUT_BYTES,
) -> subprocess.CompletedProcess[str]:
    """Capture one UTF-8 command within a time and combined output budget.

    stdin, stdout, and stderr progress concurrently. The deadline includes
    pipe drainage and process exit. Every exit path closes the pipes, kills
    the owned process group, and reaps its direct child.
    """
    require_number(timeout, "process timeout must be finite and positive", minimum=0, minimum_inclusive=False)
    require_int(max_output_bytes, "process output budget must be positive", minimum=1)
    pending = memoryview(input.encode("utf-8") if input is not None else b"")
    deadline = time.monotonic() + timeout
    output = [bytearray(), bytearray()]
    captured = 0
    process = subprocess.Popen(
        args,
        stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=env,
        start_new_session=True,
        bufsize=0,
    )
    try:
        with selectors.DefaultSelector() as selector:
            _register_pipes(selector, process, pending)

            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(args, timeout)
                for key, _ in selector.select(remaining):
                    if key.data is None:
                        pending = _write_available(key.fd, pending)
                        if not pending:
                            selector.unregister(key.fd)
                            assert process.stdin is not None
                            process.stdin.close()
                        continue
                    try:
                        chunk = os.read(key.fd, _CHUNK_BYTES)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(key.fd)
                        continue
                    captured += len(chunk)
                    if captured > max_output_bytes:
                        raise ProcessOutputError(f"command output exceeds the {max_output_bytes}-byte limit")
                    output[key.data].extend(chunk)

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(args, timeout)
            returncode = process.wait(timeout=remaining)
        try:
            stdout, stderr = (data.decode("utf-8") for data in output)
        except UnicodeDecodeError as error:
            raise ProcessOutputError("command output contains invalid UTF-8") from error
        return subprocess.CompletedProcess(args, returncode, stdout, stderr)
    finally:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        for pipe in (process.stdin, process.stdout, process.stderr):
            if pipe is not None:
                pipe.close()
        process.wait()
