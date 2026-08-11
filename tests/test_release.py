from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from autolean.release import (
    ReleaseIdentity,
    ReleaseIdentityError,
    artifact_identity,
    identity_from_git,
    release_manifest,
    write_manifest,
)

COMMIT = "abcdef0123456789abcdef0123456789abcdef01"


def test_release_identity_uses_utc_day_and_twelve_commit_characters() -> None:
    identity = ReleaseIdentity(
        commit=COMMIT,
        committed_at=datetime(2026, 8, 11, 16, 5, tzinfo=UTC),
    )

    assert identity.hashver == "2026.08.11+abcdef012345"
    assert identity.tag == "v2026.08.11+abcdef012345"
    assert identity.timestamp == "2026-08-11T16:05:00Z"


@pytest.mark.parametrize(
    "commit",
    ["short", "A" * 40, "g" * 40, "a" * 41],
)
def test_release_identity_requires_a_full_lowercase_commit(commit: str) -> None:
    with pytest.raises(ReleaseIdentityError, match="full lowercase Git object ID"):
        ReleaseIdentity(commit=commit, committed_at=datetime.now(UTC))


def test_release_identity_accepts_a_sha256_repository_commit() -> None:
    commit = "a" * 64

    identity = ReleaseIdentity(commit=commit, committed_at=datetime(2026, 8, 11, tzinfo=UTC))

    assert identity.hashver == "2026.08.11+aaaaaaaaaaaa"


def test_artifact_identity_hashes_exact_bytes(tmp_path: Path) -> None:
    artifact = tmp_path / "autolean.whl"
    artifact.write_bytes(b"wheel bytes")

    identity = artifact_identity(artifact)

    assert identity.name == "autolean.whl"
    assert identity.size == 11
    assert identity.sha256 == hashlib.sha256(b"wheel bytes").hexdigest()


def test_release_manifest_is_sorted_and_canonical(tmp_path: Path) -> None:
    wheel = tmp_path / "autolean.whl"
    source = tmp_path / "autolean.tar.gz"
    wheel.write_bytes(b"wheel")
    source.write_bytes(b"source")
    identity = ReleaseIdentity(COMMIT, datetime(2026, 8, 11, tzinfo=UTC))
    output = tmp_path / "release-manifest.json"

    manifest = release_manifest(identity, [wheel, source])
    write_manifest(output, manifest)

    decoded = json.loads(output.read_text(encoding="utf-8"))
    assert decoded["schema"] == "autolean.release-manifest.v1"
    assert decoded["commit"] == COMMIT
    assert decoded["hashver"] == "2026.08.11+abcdef012345"
    assert [item["name"] for item in decoded["artifacts"]] == ["autolean.tar.gz", "autolean.whl"]
    assert output.read_bytes().endswith(b"\n")


def test_release_manifest_rejects_duplicate_asset_names(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "artifact").write_text("first", encoding="utf-8")
    (second / "artifact").write_text("second", encoding="utf-8")
    identity = ReleaseIdentity(COMMIT, datetime(2026, 8, 11, tzinfo=UTC))

    with pytest.raises(ReleaseIdentityError, match="names must be unique"):
        release_manifest(identity, [first / "artifact", second / "artifact"])


def test_identity_from_git_uses_resolved_commit_timestamp(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "AutoLean Test"], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "autolean@example.invalid"],
        check=True,
    )
    source = tmp_path / "source.txt"
    source.write_text("source\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "source.txt"], check=True)
    environment = {
        "GIT_AUTHOR_DATE": "2026-08-11T12:00:00Z",
        "GIT_COMMITTER_DATE": "2026-08-11T12:00:00Z",
    }
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-C", str(tmp_path), "commit", "-qm", "Source"],
        check=True,
        env={**os.environ, **environment},
    )

    identity = identity_from_git(tmp_path)

    assert identity.hashver.startswith("2026.08.11+")
    assert len(identity.commit) == 40
