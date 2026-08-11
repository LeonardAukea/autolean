"""Content identity for the Lean kernel and imported proof environment."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

_ENVIRONMENT_DOMAIN = b"autolean-proof-environment-v1\0"
_LEAN_ARTIFACT_SUFFIXES = frozenset({".olean", ".so", ".dylib", ".dll"})
_PROJECT_CONFIGS = ("lean-toolchain", "lake-manifest.json", "lakefile.lean", "lakefile.toml")


class ProofEnvironmentError(RuntimeError):
    """The proof environment cannot be identified completely."""


class _Digest(Protocol):
    def update(self, data: bytes) -> None: ...


@dataclass(frozen=True)
class ProofEnvironment:
    """A reproducible identity for one installed Lean proof closure."""

    sha256: str
    lean_version: str
    lean_toolchain: str
    manifest_sha256: str
    artifact_count: int
    dependencies: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        """Return a stable JSON-compatible representation."""
        return asdict(self)


def capture_proof_environment(project_root: Path, lean: Path) -> ProofEnvironment:
    """Hash the configured kernel, manifest, and importable compiled artifacts."""
    project_root = project_root.resolve()
    lean = lean.resolve()
    lean_version = _read_lean_version(lean)
    toolchain_path = project_root / "lean-toolchain"
    toolchain = _read_optional(toolchain_path).strip()
    if not toolchain:
        raise ProofEnvironmentError("lean-toolchain is required for proof provenance")
    manifest_path = project_root / "lake-manifest.json"
    if not manifest_path.is_file():
        raise ProofEnvironmentError("lake-manifest.json is required; run `lake update`")
    manifest_bytes = manifest_path.read_bytes()
    dependencies = _dependency_pins(manifest_bytes)

    digest = hashlib.sha256(_ENVIRONMENT_DOMAIN)
    _hash_value(digest, "lean-version", lean_version.encode())
    artifact_count = 0

    for name in _PROJECT_CONFIGS:
        path = project_root / name
        if path.is_file():
            _hash_file(digest, f"project/{name}", path)

    _hash_file(digest, "toolchain/bin/lean", lean)
    artifact_count += 1

    toolchain_root = lean.parent.parent
    artifact_count += _hash_tree(
        digest,
        "toolchain/lib/lean",
        toolchain_root / "lib" / "lean",
    )

    module_roots = sorted(
        path.resolve() for path in project_root.rglob("lib/lean") if path.is_dir() and ".lake" in path.parts
    )
    for root in module_roots:
        label = f"project/{root.relative_to(project_root)}"
        artifact_count += _hash_tree(digest, label, root)

    return ProofEnvironment(
        sha256=digest.hexdigest(),
        lean_version=lean_version,
        lean_toolchain=toolchain,
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        artifact_count=artifact_count,
        dependencies=dependencies,
    )


def sha256_text(text: str) -> str:
    """Hash exact UTF-8 text for attempt provenance."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_lean_version(lean: Path) -> str:
    try:
        result = subprocess.run(
            [str(lean), "--version"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise ProofEnvironmentError(f"could not execute the Lean kernel: {e}") from e
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ProofEnvironmentError(f"Lean version probe exited {result.returncode}: {detail or 'no detail'}")
    version = result.stdout.strip()
    if not version:
        raise ProofEnvironmentError("Lean version probe returned no version")
    return version


def _read_optional(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _dependency_pins(manifest: bytes) -> tuple[str, ...]:
    try:
        payload = json.loads(manifest)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ProofEnvironmentError(f"lake-manifest.json is invalid: {e}") from e
    packages = payload.get("packages") if isinstance(payload, dict) else None
    if not isinstance(packages, list):
        raise ProofEnvironmentError("lake-manifest.json has no package list")
    pins: list[str] = []
    for package in packages:
        if not isinstance(package, dict):
            raise ProofEnvironmentError("lake-manifest.json contains a malformed package")
        name = package.get("name")
        revision = package.get("rev") or package.get("inputRev")
        if not isinstance(name, str) or not isinstance(revision, str):
            raise ProofEnvironmentError("lake-manifest.json contains an unpinned package")
        if re.fullmatch(r"[0-9a-fA-F]{40,64}", revision) is None:
            raise ProofEnvironmentError(f"lake-manifest.json package {name!r} has a non-content revision")
        scope = package.get("scope")
        qualified = f"{scope}/{name}" if isinstance(scope, str) and scope else name
        pins.append(f"{qualified}@{revision}")
    return tuple(sorted(pins))


def _hash_tree(digest: _Digest, label: str, root: Path) -> int:
    if not root.is_dir():
        return 0
    files = sorted(
        path for path in root.rglob("*") if path.is_file() and path.suffix in _LEAN_ARTIFACT_SUFFIXES
    )
    for path in files:
        _hash_file(digest, f"{label}/{path.relative_to(root)}", path)
    return len(files)


def _hash_file(digest: _Digest, label: str, path: Path) -> None:
    _hash_value(digest, f"path:{label}", b"")
    _hash_value(digest, "size", str(path.stat().st_size).encode())
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)


def _hash_value(digest: _Digest, label: str, value: bytes) -> None:
    label_bytes = label.encode("utf-8")
    digest.update(len(label_bytes).to_bytes(8, "big"))
    digest.update(label_bytes)
    digest.update(len(value).to_bytes(8, "big"))
    digest.update(value)
