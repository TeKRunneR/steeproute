"""Git commit-hash provenance and ISO 8601 UTC timestamp helpers.

`get_commit_short` is called at every cache write (Story 2.7) and report render
(Epic 3 §FR19). It must never raise — returning the `"unknown"` sentinel when
git is unavailable keeps the codebase usable when installed from a wheel without
source. `iso8601_utc_now` is the single source of truth for the `Z`-suffixed
timestamp shape used in manifests and reports (Architecture §Serialization).
"""

from __future__ import annotations

import datetime
import pathlib
import subprocess

# Directory git inspects in production. The helper is split into a `_at(cwd)`
# inner so tests can target a throwaway git tree without leaking a cwd
# parameter into the public API.
_PACKAGE_ROOT: pathlib.Path = pathlib.Path(__file__).parent

# Returned when git is absent or the package is outside any repo. A sentinel
# string keeps the manifest field shape stable instead of forcing every caller
# to handle Optional.
_UNKNOWN_COMMIT_SENTINEL: str = "unknown"


def get_commit_short() -> str:
    """Return the short commit hash, with `-dirty` suffix if the tree is modified.

    Falls back to `"unknown"` when git is not on PATH or the package is not
    inside a repository. Never raises.
    """
    return _get_commit_short_at(_PACKAGE_ROOT)


def _get_commit_short_at(cwd: pathlib.Path) -> str:
    """Resolve commit hash + dirty flag against an arbitrary `cwd`.

    Production calls with `_PACKAGE_ROOT`; tests target a throwaway repo.
    `core.fileMode=false` is passed inline so a stale-execute-bit difference
    on Windows checkouts doesn't spuriously flip the dirty flag.

    `--untracked-files=no` excludes untracked-only changes from the dirty
    signal (deferred-work D1 from Story 2.6): a typical `bmad-dev-story` run
    leaves story / planning artifacts in the working tree, and that should not
    flip the report-visible commit string to `-dirty` when no tracked file was
    modified. Architecture's "dirty if working tree modified" is interpreted
    as "tracked files modified" — same convention `git describe --dirty` uses.
    """
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            cwd=cwd,
        ).stdout.strip()
        status = subprocess.run(
            [
                "git",
                "-c",
                "core.fileMode=false",
                "status",
                "--porcelain",
                "--untracked-files=no",
            ],
            check=True,
            capture_output=True,
            text=True,
            cwd=cwd,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return _UNKNOWN_COMMIT_SENTINEL
    return f"{commit}-dirty" if status else commit


def iso8601_utc_now() -> str:
    """Current UTC time as `"YYYY-MM-DDTHH:MM:SSZ"` (second precision, literal Z)."""
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
