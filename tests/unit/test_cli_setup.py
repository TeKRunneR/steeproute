"""Unit tests for the setup CLI's non-pipeline helpers.

`emit_osm_age_warning` (Story 2.9) originally lived in `cli/setup.py`; Story 2.10
lifted it to `cli/_shared.py` so `cli/query.py`'s cache-hit path could reuse the
same boundary semantics. Its tests retain their original location to avoid churn —
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

from steeproute.cache import Manifest
from steeproute.cli._shared import configure_cli_logging, emit_osm_age_warning
from steeproute.cli.setup import _configure_osmnx_logging  # pyright: ignore[reportPrivateUsage]
from steeproute.models import Area


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
    """AC #6: an `osm_extract_date` dated >90 days ago triggers a single `logging.warning`."""
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
    """AC #6: a fresh `osm_extract_date` (0 days old) emits no warning."""
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
    """AC #6 boundary: `age == threshold` does NOT warn; `age > threshold` does."""
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

    `Manifest.from_dict` (Story 2.7) raises `CacheCorruptedError` on schema
    violations before this helper ever sees the manifest, so reaching this branch
    requires the user hand-editing the file mid-run. The age warning is auxiliary
    diagnostic information — losing it on a malformed date is acceptable; crashing
    the cache-hit happy path because of it is not.
    """
    manifest = _manifest_with("not-an-iso-timestamp")
    with caplog.at_level(logging.WARNING, logger="steeproute.cli._shared"):
        emit_osm_age_warning(manifest=manifest, threshold_days=90, now=_NOW)
    assert caplog.records == []


# --- `_configure_osmnx_logging` (setup observability) -------------------------
#
# osmnx logs its Overpass cache hit at INFO through its own `utils.log`, which by
# default reaches neither the stdlib `logging` tree nor any stream. The setup CLI
# opts in via `settings.log_file` + a pre-attached `NullHandler` — see the function's
# docstring for why that specific pair. These tests pin the three properties the
# arrangement depends on, all of which are osmnx-internal behaviour we don't control.


@pytest.fixture
def isolated_osmnx_logging(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> Iterator[logging.Logger]:
    """Yield osmnx's logger with all touched global state restored afterwards.

    `osmnx.settings` is module-level and both the osmnx and root loggers are
    process-global, so without this a run of these tests would leak into every
    later test in the session — including the root level and handler set, which
    `configure_cli_logging`'s `basicConfig(force=True)` rebinds wholesale.
    `logs_folder` is redirected into `tmp_path` so that a regression which *does*
    let osmnx build its `FileHandler` shows up as a file under `tmp_path` rather
    than polluting the repo.
    """
    monkeypatch.setattr(osmnx.settings, "log_file", False)
    monkeypatch.setattr(osmnx.settings, "log_console", False)
    monkeypatch.setattr(osmnx.settings, "logs_folder", str(tmp_path / "logs"))
    logger = logging.getLogger(osmnx.settings.log_name)
    root = logging.getLogger()
    saved = [(lg, list(lg.handlers), lg.level) for lg in (logger, root)]
    logger.handlers.clear()
    try:
        yield logger
    finally:
        for lg, handlers, level in saved:
            lg.handlers[:] = handlers
            lg.setLevel(level)


def test_configure_osmnx_logging_opts_into_the_logging_tree_only(
    isolated_osmnx_logging: logging.Logger, tmp_path: pathlib.Path
) -> None:
    """`log_file` on (records reach `logging`), `log_console` off (stdout stays ours).

    `log_console = True` would `print` to `sys.__stdout__`, bypassing redirection and
    colliding with the run summary that owns stdout (Architecture §Cat 8).
    """
    _configure_osmnx_logging()

    assert osmnx.settings.log_file is True
    assert osmnx.settings.log_console is False
    # A handler is present, so osmnx's `_get_logger` short-circuits instead of
    # attaching its own `FileHandler` and creating `logs/`.
    assert isolated_osmnx_logging.handlers != []
    assert not (tmp_path / "logs").exists()


def test_configure_osmnx_logging_leaves_level_inherited_from_root(
    isolated_osmnx_logging: logging.Logger,
) -> None:
    """Verbosity comes from the root level, not from a level set on osmnx's logger.

    That inheritance IS the `--verbose` mechanism: `configure_cli_logging` puts the
    root at DEBUG or WARNING, and osmnx's INFO records pass or are dropped
    accordingly — no verbose flag is threaded into `_configure_osmnx_logging`.
    """
    _configure_osmnx_logging()

    assert isolated_osmnx_logging.level == logging.NOTSET
    configure_cli_logging(verbose=False)
    assert isolated_osmnx_logging.isEnabledFor(logging.INFO) is False
    configure_cli_logging(verbose=True)
    assert isolated_osmnx_logging.isEnabledFor(logging.INFO) is True


def test_configure_osmnx_logging_routes_osmnx_log_calls_into_logging(
    isolated_osmnx_logging: logging.Logger,
    caplog: pytest.LogCaptureFixture,
    tmp_path: pathlib.Path,
) -> None:
    """An `osmnx.utils.log(...)` INFO call — the cache-hit line's own path — arrives as a record."""
    _configure_osmnx_logging()

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
    _configure_osmnx_logging()
    _configure_osmnx_logging()
    _configure_osmnx_logging()

    assert len(isolated_osmnx_logging.handlers) == 1
