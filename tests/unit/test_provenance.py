# pyright: reportPrivateUsage=false
# Reason: tests deliberately import `_get_commit_short_at` + `_UNKNOWN_COMMIT_SENTINEL`
# — the cwd-parameterized helper is private to the public API per Story 2.6 AC #7's
# "testable against a chosen working directory without leaking that arg into the
# public API" requirement; the sentinel is a stable contract value tests need to
# assert against. Same per-file relaxation pattern as
# tests/integration/test_pipeline_end_to_end.py.
"""Unit tests for provenance.get_commit_short, _get_commit_short_at, iso8601_utc_now.

Uses real git via subprocess against pytest's tmp_path — no `subprocess` mocking
per Architecture §Cat 11e ("real git behavior preferred over mocking subprocess").
"""

from __future__ import annotations

import datetime
import pathlib
import re
import subprocess

import pytest

from steeproute.provenance import (
    _UNKNOWN_COMMIT_SENTINEL,
    _get_commit_short_at,
    get_commit_short,
    iso8601_utc_now,
)

_ISO8601_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_CLEAN_HASH_RE = re.compile(r"^[0-9a-f]{7,40}$")
_DIRTY_HASH_RE = re.compile(r"^[0-9a-f]{7,40}-dirty$")
_CLEAN_OR_DIRTY_RE = re.compile(r"^[0-9a-f]{7,40}(-dirty)?$")


def _init_throwaway_repo(repo: pathlib.Path) -> pathlib.Path:
    """Create a fresh git repo at `repo` with one committed tracked file.

    Local `user.name`/`user.email` are set so the commit succeeds on hosts
    without global git identity (e.g. fresh CI runners). `commit.gpgsign` is
    pinned to `false` inline so developer machines with `commit.gpgsign=true`
    globally don't crash the commit (the throwaway email won't have a matching
    signing key).
    """
    subprocess.run(["git", "init", "-q"], check=True, cwd=repo)
    subprocess.run(
        ["git", "config", "user.email", "story-26-test@example.com"],
        check=True,
        cwd=repo,
    )
    subprocess.run(
        ["git", "config", "user.name", "Story 2.6 Test"],
        check=True,
        cwd=repo,
    )
    tracked = repo / "tracked.txt"
    tracked.write_text("initial content\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], check=True, cwd=repo)
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "initial commit"],
        check=True,
        cwd=repo,
    )
    return tracked


def test_get_commit_short_at_returns_clean_hash_for_fresh_commit(
    tmp_path: pathlib.Path,
) -> None:
    _init_throwaway_repo(tmp_path)
    result = _get_commit_short_at(tmp_path)
    assert _CLEAN_HASH_RE.match(result) is not None, result


def test_get_commit_short_at_appends_dirty_when_working_tree_modified(
    tmp_path: pathlib.Path,
) -> None:
    tracked = _init_throwaway_repo(tmp_path)
    tracked.write_text("modified content\n", encoding="utf-8")
    result = _get_commit_short_at(tmp_path)
    assert _DIRTY_HASH_RE.match(result) is not None, result


def test_get_commit_short_at_returns_unknown_sentinel_for_non_git_directory(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stop git from walking up to a possible parent repo (e.g. if tmp lives inside another checkout)."""
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    assert _get_commit_short_at(tmp_path) == _UNKNOWN_COMMIT_SENTINEL


def test_get_commit_short_real_repo_matches_clean_or_dirty_hash_pattern() -> None:
    """Smoke-test the production helper against the actual checked-out repo.

    Catches regressions in cwd resolution that the tmp-repo tests miss. The
    repo may be clean or dirty at the moment of execution; either shape is
    acceptable. `unknown` is also accepted so the test stays green when run
    from an installed wheel without source.
    """
    result = get_commit_short()
    assert _CLEAN_OR_DIRTY_RE.match(result) is not None or result == _UNKNOWN_COMMIT_SENTINEL, (
        result
    )


def test_iso8601_utc_now_matches_z_suffixed_second_precision_and_round_trips() -> None:
    result = iso8601_utc_now()
    assert _ISO8601_RE.match(result) is not None, result
    parsed = datetime.datetime.fromisoformat(result)
    delta = datetime.datetime.now(datetime.UTC) - parsed
    assert abs(delta.total_seconds()) < 5
