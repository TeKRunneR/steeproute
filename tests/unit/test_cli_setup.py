# pyright: reportPrivateUsage=false
# Reason: pins `cli/setup.py`'s private osmnx-logging helpers, and the drift canary
# drives osmnx's own private `_http` cache functions on purpose.
"""Unit tests for the setup CLI's non-pipeline helpers.

`emit_osm_age_warning` lives in `cli/_shared.py` so both CLIs' cache-hit paths
share one set of boundary semantics. Its tests live here rather than beside it —
they pin the helper's contract regardless of its host module. The second half of
this file covers `cli/setup.py::_configure_osmnx_logging`.
"""

from __future__ import annotations

import datetime
import logging
import pathlib
from collections.abc import Iterator

import osmnx
import pytest

# The drift canary below drives osmnx's real cache read; `_http` is private and not
# re-exported, so it must be imported as a submodule rather than off the package.
from osmnx import _http as osmnx_http

from steeproute.cache import Manifest
from steeproute.cli._shared import emit_osm_age_warning
from steeproute.cli.setup import (
    _configure_osmnx_logging,
    _install_osmnx_fetch_reporter,
)
from steeproute.models import Area
from steeproute.progress import StageProgress


def _manifest_with(osm_extract_date: str) -> Manifest:
    """Build a synthetic `Manifest` with the given `osm_extract_date`.

    All other fields are arbitrary but valid: `emit_osm_age_warning` only
    reads `osm_extract_date`, so the rest are stable placeholder content.
    """
    return Manifest(
        area=Area(center=(45.0716, 6.1079), radius_km=2.0),
        untagged_policy="include",
        dem_version="test-dem-1",
        pipeline_content_hash="a" * 64,
        osm_extract_date=osm_extract_date,
        cache_key_hash="0123456789abcdef",
        steeproute_version="0.1.0",
        steeproute_commit="abc1234",
        created_at=osm_extract_date,
    )


# Fixed "now" so age math is deterministic across the test file.
_NOW = datetime.datetime(2026, 5, 22, 12, 0, 0, tzinfo=datetime.UTC)


def _iso_days_before(days: float) -> str:
    """ISO-8601 Z-suffix timestamp `days` ago from `_NOW`."""
    dt = _NOW - datetime.timedelta(days=days)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_emit_osm_age_warning_warns_when_age_exceeds_threshold(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An `osm_extract_date` dated >90 days ago triggers a single `logging.warning`."""
    manifest = _manifest_with(_iso_days_before(120))
    with caplog.at_level(logging.WARNING, logger="steeproute.cli._shared"):
        emit_osm_age_warning(manifest=manifest, threshold_days=90, now=_NOW)

    warnings = [rec for rec in caplog.records if rec.levelno == logging.WARNING]
    assert len(warnings) == 1
    msg = warnings[0].getMessage()
    assert "120" in msg
    assert "90" in msg
    assert "--force-refresh" in msg


def test_emit_osm_age_warning_silent_below_threshold(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A fresh `osm_extract_date` (0 days old) emits no warning."""
    manifest = _manifest_with(_iso_days_before(0))
    with caplog.at_level(logging.WARNING, logger="steeproute.cli._shared"):
        emit_osm_age_warning(manifest=manifest, threshold_days=90, now=_NOW)
    assert caplog.records == []


@pytest.mark.parametrize(
    ("age_days", "should_warn"),
    [
        # Threshold semantics are **strict** per the helper docstring: only
        # `age > threshold_days` warns. 90.0 days exactly = no warn; just over
        # = warn. Pinning these two cases freezes the boundary against drift.
        (89.0, False),
        (90.0, False),
        (90.5, True),
        (91.0, True),
    ],
)
def test_emit_osm_age_warning_boundary_semantics(
    caplog: pytest.LogCaptureFixture,
    age_days: float,
    should_warn: bool,
) -> None:
    """Boundary: `age == threshold` does NOT warn; `age > threshold` does."""
    manifest = _manifest_with(_iso_days_before(age_days))
    with caplog.at_level(logging.WARNING, logger="steeproute.cli._shared"):
        emit_osm_age_warning(manifest=manifest, threshold_days=90, now=_NOW)
    if should_warn:
        assert len(caplog.records) == 1
    else:
        assert caplog.records == []


def test_emit_osm_age_warning_custom_threshold(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A non-default `threshold_days` (e.g. user passed `--osm-age-warn-days 30`) is honoured."""
    manifest = _manifest_with(_iso_days_before(60))
    with caplog.at_level(logging.WARNING, logger="steeproute.cli._shared"):
        emit_osm_age_warning(manifest=manifest, threshold_days=30, now=_NOW)
    assert len(caplog.records) == 1


def test_emit_osm_age_warning_swallows_malformed_extract_date(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A manifest with a malformed `osm_extract_date` does not crash the cache-hit path.

    `Manifest.from_dict` raises `CacheCorruptedError` on schema
    violations before this helper ever sees the manifest, so reaching this branch
    requires the user hand-editing the file mid-run. The age warning is auxiliary
    diagnostic information — losing it on a malformed date is acceptable; crashing
    the cache-hit happy path because of it is not.
    """
    manifest = _manifest_with("not-an-iso-timestamp")
    with caplog.at_level(logging.WARNING, logger="steeproute.cli._shared"):
        emit_osm_age_warning(manifest=manifest, threshold_days=90, now=_NOW)
    assert caplog.records == []


# `_configure_osmnx_logging` (setup observability)
# # osmnx logs its Overpass cache hit at INFO through its own `utils.log`, which by
# default reaches neither the stdlib `logging` tree nor any stream. The setup CLI
# opts in via `settings.log_file` + a pre-attached `NullHandler` — see the function's
# docstring for why that specific pair. These tests pin the three properties the
# arrangement depends on, all of which are osmnx-internal behaviour we don't control.


@pytest.fixture
def isolated_osmnx_logging(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> Iterator[logging.Logger]:
    """Yield osmnx's logger with all touched global state restored afterwards.

    `osmnx.settings` is module-level and the logger is process-global, so without
    this a run of these tests would leak into every later test in the session —
    handlers, level, and `propagate`, all three of which
    `_configure_osmnx_logging` / `_install_osmnx_fetch_reporter` mutate.
    `logs_folder` is redirected into `tmp_path` so that a regression which *does*
    let osmnx build its `FileHandler` shows up as a file under `tmp_path` rather
    than polluting the repo.
    """
    monkeypatch.setattr(osmnx.settings, "log_file", False)
    monkeypatch.setattr(osmnx.settings, "log_console", False)
    monkeypatch.setattr(osmnx.settings, "logs_folder", str(tmp_path / "logs"))
    logger = logging.getLogger(osmnx.settings.log_name)
    saved_handlers, saved_level, saved_propagate = (
        list(logger.handlers),
        logger.level,
        logger.propagate,
    )
    logger.handlers.clear()
    try:
        yield logger
    finally:
        logger.handlers[:] = saved_handlers
        logger.setLevel(saved_level)
        logger.propagate = saved_propagate


def test_configure_osmnx_logging_opts_into_the_logging_tree_only(
    isolated_osmnx_logging: logging.Logger, tmp_path: pathlib.Path
) -> None:
    """`log_file` on (records reach `logging`), `log_console` off (stdout stays ours).

    `log_console = True` would `print` to `sys.__stdout__`, bypassing redirection and
    colliding with the run summary that owns stdout (Architecture §Cat 8).
    """
    _configure_osmnx_logging(verbose=False)

    assert osmnx.settings.log_file is True
    assert osmnx.settings.log_console is False
    # A handler is present, so osmnx's `_get_logger` short-circuits instead of
    # attaching its own `FileHandler` and creating `logs/`.
    assert isolated_osmnx_logging.handlers != []
    assert not (tmp_path / "logs").exists()


@pytest.mark.parametrize("verbose", [False, True])
def test_configure_osmnx_logging_admits_info_and_gates_stderr_on_propagate(
    isolated_osmnx_logging: logging.Logger, verbose: bool
) -> None:
    """INFO always reaches handlers; only `--verbose` propagates it on to stderr.

    The reporter needs osmnx's INFO records on a default run, and a logger drops
    sub-level records before any handler sees them — hence a pinned INFO level with
    `propagate` (not the level) deciding whether the raw text hits the root's stderr
    handler.
    """
    _configure_osmnx_logging(verbose=verbose)

    assert isolated_osmnx_logging.level == logging.INFO
    assert isolated_osmnx_logging.isEnabledFor(logging.INFO) is True
    assert isolated_osmnx_logging.propagate is verbose


def test_configure_osmnx_logging_routes_osmnx_log_calls_into_logging(
    isolated_osmnx_logging: logging.Logger,
    caplog: pytest.LogCaptureFixture,
    tmp_path: pathlib.Path,
) -> None:
    """An `osmnx.utils.log(...)` INFO call — the cache-hit line's own path — arrives as a record."""
    _configure_osmnx_logging(verbose=True)

    with caplog.at_level(logging.INFO, logger=osmnx.settings.log_name):
        osmnx.utils.log("Retrieved response from cache file 'x.json'", logging.INFO)

    assert [rec.getMessage() for rec in caplog.records] == [
        "Retrieved response from cache file 'x.json'"
    ]
    # The record came through osmnx's own logger, not some incidental one.
    assert caplog.records[0].name == isolated_osmnx_logging.name
    assert not (tmp_path / "logs").exists()


def test_configure_osmnx_logging_is_idempotent(
    isolated_osmnx_logging: logging.Logger,
) -> None:
    """Repeated calls (one per `CliRunner` invocation in a test process) don't stack handlers."""
    _configure_osmnx_logging(verbose=False)
    _configure_osmnx_logging(verbose=False)
    _configure_osmnx_logging(verbose=False)

    assert len(isolated_osmnx_logging.handlers) == 1


# `_OsmnxFetchReporter` (cache-hit visibility without --verbose)
def _reporting_progress(*, quiet: bool = False) -> list[str]:
    """Install a reporter writing into a fresh line list; return the list."""
    lines: list[str] = []
    _configure_osmnx_logging(verbose=False)
    _install_osmnx_fetch_reporter(StageProgress(on_line=None if quiet else lines.append))
    return lines


@pytest.mark.usefixtures("isolated_osmnx_logging")
def test_fetch_reporter_announces_a_cache_hit_on_the_progress_sink() -> None:
    """AC: the outcome shows WITHOUT `--verbose` — the reporter writes to the stdout sink."""
    lines = _reporting_progress()

    osmnx.utils.log("Retrieved response from cache file 'abc.json'", logging.INFO)

    assert lines == ["  osm: Overpass response served from cache (no download)"]


@pytest.mark.usefixtures("isolated_osmnx_logging")
def test_fetch_reporter_forwards_a_real_download_verbatim() -> None:
    """A live fetch keeps osmnx's own wording — size and host are the useful detail."""
    lines = _reporting_progress()

    osmnx.utils.log("Downloaded 516.2kB from 'overpass-api.de' with status 200", logging.INFO)

    assert lines == ["  osm: Downloaded 516.2kB from 'overpass-api.de' with status 200"]


@pytest.mark.usefixtures("isolated_osmnx_logging")
def test_fetch_reporter_reports_each_outcome_once() -> None:
    """A multi-request fetch must not repeat itself; both kinds may still appear."""
    lines = _reporting_progress()

    for _ in range(3):
        osmnx.utils.log("Retrieved response from cache file 'a.json'", logging.INFO)
        osmnx.utils.log("Downloaded 1.0kB from 'overpass-api.de' with status 200", logging.INFO)

    assert lines == [
        "  osm: Overpass response served from cache (no download)",
        "  osm: Downloaded 1.0kB from 'overpass-api.de' with status 200",
    ]


@pytest.mark.usefixtures("isolated_osmnx_logging")
def test_fetch_reporter_is_silent_under_quiet() -> None:
    """`--quiet` installs no sink, so the line disappears with every other progress line."""
    lines = _reporting_progress(quiet=True)

    osmnx.utils.log("Retrieved response from cache file 'abc.json'", logging.INFO)

    assert lines == []


@pytest.mark.usefixtures("isolated_osmnx_logging")
def test_fetch_reporter_ignores_osmnx_chatter() -> None:
    """Only the two fetch-outcome messages are progress-worthy; the other ~30 are not."""
    lines = _reporting_progress()

    osmnx.utils.log("Requesting data from API in 1 request(s)", logging.INFO)
    osmnx.utils.log("Created graph with 4,963 nodes and 10,069 edges", logging.INFO)
    osmnx.utils.log("Retrieved all data from API in 1 request(s)", logging.INFO)

    assert lines == []


@pytest.mark.usefixtures("isolated_osmnx_logging")
def test_fetch_reporter_reinstall_drops_the_previous_runs_sink() -> None:
    """Two `CliRunner` invocations in one process must not write into the first run's list."""
    first = _reporting_progress()
    second = _reporting_progress()

    osmnx.utils.log("Retrieved response from cache file 'abc.json'", logging.INFO)

    assert first == []
    assert second == ["  osm: Overpass response served from cache (no download)"]


@pytest.mark.usefixtures("isolated_osmnx_logging")
def test_fetch_reporter_matches_osmnxs_real_cache_read_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """Drift canary: drive osmnx's OWN cache read, not a hand-written message.

    `_OSMNX_CACHE_HIT_MARKER` is a substring of a private, undocumented log line. If
    an osmnx upgrade rewords it, the reporter would silently go quiet — the failure
    mode this whole change exists to remove. Writing a cache file through osmnx's own
    `_save_to_cache` and reading it back through `_retrieve_from_cache` keeps that
    honest, offline (both touch only the filesystem).
    """
    monkeypatch.setattr(osmnx.settings, "use_cache", True)
    monkeypatch.setattr(osmnx.settings, "cache_folder", str(tmp_path / "osmnx"))
    lines = _reporting_progress()

    url = "http://overpass-api.de/api/interpreter?data=fixture"
    osmnx_http._save_to_cache(url, {"elements": []}, ok=True)
    assert osmnx_http._retrieve_from_cache(url) == {"elements": []}, "cache write/read failed"

    assert lines == ["  osm: Overpass response served from cache (no download)"]
