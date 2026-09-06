"""Profile local AutoLean workloads with cProfile and tracemalloc."""

from __future__ import annotations

import argparse
import cProfile
import hashlib
import json
import platform
import pstats
import resource
import statistics
import sys
import time
import tracemalloc
from collections.abc import Callable
from pathlib import Path

from autolean.export import _source_files
from autolean.lean_interface import LeanProject
from autolean.scanner import lean_source_files, scan_project
from autolean.structure import LeanStructureProvider


def _workload(name: str, root: Path) -> Callable[[], object]:
    if name == "scan":
        return lambda: [(target.id, target.source_sha256) for target in scan_project(root)]
    if name == "export":
        return lambda: [path.relative_to(root).as_posix() for path in _source_files(root)]
    if name == "environment":
        project = LeanProject(root)
        return lambda: project.proof_environment(refresh=True).as_dict()
    provider = LeanStructureProvider()
    sources = [(path, path.read_text(encoding="utf-8")) for path in lean_source_files(root)]
    return lambda: [provider.inspect(path, source, line=1, col=0).sha256 for path, source in sources]


def _identity(value: object) -> str:
    content = json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path("workspace"))
    parser.add_argument("--workload", choices=("scan", "export", "structure", "environment"), default="scan")
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be positive")
    root = args.project.resolve(strict=True)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    workload = _workload(args.workload, root)

    started = time.perf_counter()
    expected = _identity(workload())
    cold_seconds = time.perf_counter() - started

    def checked() -> None:
        if _identity(workload()) != expected:
            raise RuntimeError("workload output changed during profiling")

    elapsed: list[float] = []
    for _ in range(args.repeat):
        started = time.perf_counter()
        checked()
        elapsed.append(time.perf_counter() - started)

    profile = cProfile.Profile()
    profile.runcall(checked)
    profile.dump_stats(output / f"{args.workload}.pstats")
    with (output / f"{args.workload}-cpu.txt").open("w") as stream:
        pstats.Stats(profile, stream=stream).strip_dirs().sort_stats("cumulative").print_stats(30)

    tracemalloc.start(8)
    checked()
    current, peak = tracemalloc.get_traced_memory()
    snapshot = tracemalloc.take_snapshot()
    tracemalloc.stop()
    with (output / f"{args.workload}-memory.txt").open("w") as stream:
        for item in snapshot.statistics("lineno")[:30]:
            print(item, file=stream)
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    record = {
        "schema": "autolean.runtime-profile.v1",
        "workload": args.workload,
        "project": str(root),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "output_sha256": expected,
        "cold_seconds": cold_seconds,
        "warm_seconds": elapsed,
        "warm_median_seconds": statistics.median(elapsed),
        "traced_retained_bytes": current,
        "traced_peak_bytes": peak,
        "process_peak_rss_bytes": rss if sys.platform == "darwin" else rss * 1024,
    }
    (output / f"{args.workload}.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
