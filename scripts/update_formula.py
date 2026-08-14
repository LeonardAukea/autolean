#!/usr/bin/env python3
"""Point the Homebrew formula at one immutable release."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import urllib.parse
from collections.abc import Sequence
from pathlib import Path

FORMULA = Path(__file__).resolve().parents[1] / "Formula" / "autolean.rb"
REPOSITORY = "LeonardAukea/autolean"


def _release_manifest(tag: str) -> dict[str, object]:
    """Read the manifest GitHub published with one release."""
    result = subprocess.run(
        ["gh", "release", "view", tag, "--repo", REPOSITORY, "--json", "assets"],
        capture_output=True,
        text=True,
        check=True,
    )
    if not json.loads(result.stdout).get("assets"):
        raise SystemExit(f"release {tag} has no assets")
    manifest = subprocess.run(
        [
            "gh",
            "release",
            "download",
            tag,
            "--repo",
            REPOSITORY,
            "--pattern",
            "release-manifest.json",
            "--output",
            "-",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    record = json.loads(manifest.stdout)
    if not isinstance(record, dict):
        raise SystemExit("release-manifest.json is not an object")
    return record


def _sdist(manifest: dict[str, object]) -> tuple[str, str]:
    """Return the source distribution's name and SHA-256."""
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise SystemExit("release manifest lists no artifacts")
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        name = artifact.get("name")
        digest = artifact.get("sha256")
        if isinstance(name, str) and name.endswith(".tar.gz") and isinstance(digest, str):
            return name, digest
    raise SystemExit("release manifest has no source distribution")


def update(tag: str) -> bool:
    """Rewrite the formula's pinned release. Returns whether it changed."""
    name, digest = _sdist(_release_manifest(tag))
    url = f"https://github.com/{REPOSITORY}/releases/download/{urllib.parse.quote(tag, safe='')}/{name}"
    text = FORMULA.read_text(encoding="utf-8")
    updated = re.sub(r'(?m)^  url ".*"$', f'  url "{url}"', text, count=1)
    updated = re.sub(r'(?m)^  sha256 ".*"$', f'  sha256 "{digest}"', updated, count=1)
    if updated == text:
        return False
    FORMULA.write_text(updated, encoding="utf-8")
    return True


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", help="immutable Hashver tag, e.g. v2026.08.13+abcdef012345")
    options = parser.parse_args(arguments)
    changed = update(options.tag)
    print(f"{FORMULA.name}: {'updated to' if changed else 'already at'} {options.tag}")
    print("Regenerate resources when a runtime dependency changed:")
    print("  brew update-python-resources Formula/autolean.rb")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
