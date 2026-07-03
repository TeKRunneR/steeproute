# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# pyright: reportPrivateUsage=false, reportUnannotatedClassAttribute=false
# Reason: rasterio surfaces DatasetReader / read() as Unknown; same external-boundary
# pattern as pipeline/dem.py and its test. The tests exercise private DEM helpers
# (`_tile_ranges` etc.) by design, and the in-test mock-response classes need no
# attribute annotations. (The urlopen-mock callbacks' unused `req`/`timeout` are
# suppressed inline per-line, so a genuinely-unused param elsewhere still gets flagged.)
"""Offline unit tests for `pipeline.dem_download.resolve_dem`.

The IGN WMS is never contacted: `urlopen` is monkeypatched to return synthetic
BIL float32 payloads sized to each request's `width`/`height`. Covers the I/O &
edge-case matrix from `spec-dem-auto-download.md` — single-tile happy path,
multi-tile mosaic placement, cache reuse, `force_refresh`, network-error
mapping, and bad-payload-size mapping. The live IGN fetch lives in
`tests/integration/test_dem_live.py`.
"""

from __future__ import annotations

import math
import pathlib
import urllib.error
import urllib.parse
from typing import Any

import networkx as nx
import numpy as np
import pytest
import rasterio
import shapely

from steeproute.errors import DataSourceUnavailableError
from steeproute.models import Area
from steeproute.pipeline import dem_download
from steeproute.pipeline.dem_download import graph_dem_bounds, resolve_dem
from steeproute.progress import StageProgress

_AREA = Area(center=(45.260, 5.788), radius_km=0.05)
# `resolve_dem` is bbox-driven (the setup path derives bounds from graph geometry);
# these mechanics tests exercise it with a realistic area-derived bbox.
_BOUNDS = dem_download._padded_bbox(_AREA)


class _FakeHeaders:
    """Stand-in for `HTTPResponse.headers` exposing `get_content_type()`."""

    def __init__(self, content_type: str) -> None:
        self._content_type = content_type

    def get_content_type(self) -> str:
        return self._content_type


class _FakeResp:
    """Minimal context-manager stand-in for an `http.client.HTTPResponse`."""

    def __init__(self, body: bytes, content_type: str = "application/octet-stream") -> None:
        self._body = body
        self.headers = _FakeHeaders(content_type)

    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._body


def _make_fake_urlopen(call_log: list[tuple[int, int]]) -> Any:
    """Return a fake `urlopen` that fills each tile with its 0-based call index.

    Recording `(width, height)` per call lets tests assert the tiling decomposition;
    the per-tile constant fill lets them verify mosaic placement and orientation.
    """

    def fake(req: Any, timeout: float | None = None) -> _FakeResp:  # noqa: ARG001  # pyright: ignore[reportUnusedParameter]
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(req.full_url).query)
        width = int(qs["width"][0])
        height = int(qs["height"][0])
        value = len(call_log)
        call_log.append((width, height))
        arr = np.full((height, width), float(value), dtype="<f4")
        return _FakeResp(arr.tobytes())

    return fake


def _read_band(path: pathlib.Path) -> np.ndarray:
    with rasterio.open(path) as dataset:
        return dataset.read(1)


def test_single_tile_writes_geotiff_under_dem_cache(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Happy path: one WMS request, one WGS84 float32 GeoTIFF under `steeproute/dem/`."""
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(dem_download, "urlopen", _make_fake_urlopen(calls))

    path = resolve_dem(_BOUNDS, tmp_path)

    assert len(calls) == 1
    assert path.is_file()
    assert path.parent == tmp_path / "steeproute" / "dem"
    with rasterio.open(path) as dataset:
        assert dataset.crs.to_epsg() == 4326
        assert dataset.count == 1
        assert dataset.dtypes[0] == "float32"


def test_multi_tile_mosaic_places_tiles_north_up(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A grid larger than one tile is fetched as multiple tiles and stitched, north-up."""
    monkeypatch.setattr(dem_download, "_MAX_TILE_PX", 16)
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(dem_download, "urlopen", _make_fake_urlopen(calls))

    # Compute the expected tile count from the same helpers the implementation uses.
    west, south, east, north = dem_download._padded_bbox(_AREA)
    width, height = dem_download._grid_dims(west, south, east, north, _AREA.center[0])
    n_cols = len(list(dem_download._tile_ranges(width)))
    n_rows = len(list(dem_download._tile_ranges(height)))
    assert n_cols > 1 and n_rows > 1, "test area must span more than one tile"

    path = resolve_dem(_BOUNDS, tmp_path)

    assert len(calls) == n_rows * n_cols
    band = _read_band(path)
    assert band.shape == (height, width)
    # Iteration is row-major (north→south outer, west→east inner) with fill =
    # call index, so the NW pixel carries call 0 and the SE pixel the last call.
    assert band[0, 0] == 0.0
    assert band[-1, -1] == float(n_rows * n_cols - 1)
    # Every tile landed: the distinct fill values are exactly 0..N-1.
    assert set(np.unique(band).tolist()) == {float(i) for i in range(n_rows * n_cols)}


def test_multi_tile_fetch_emits_tile_progress_lines(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Story 11.1 (FR33): the tile loop reports `tile i/N` through the stage seam."""
    monkeypatch.setattr(dem_download, "_MAX_TILE_PX", 16)
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(dem_download, "urlopen", _make_fake_urlopen(calls))
    lines: list[str] = []
    progress = StageProgress(lines.append)

    resolve_dem(_BOUNDS, tmp_path, progress=progress)

    total = len(calls)
    assert total > 1, "test area must span more than one tile"
    assert lines == [f"  tile {i}/{total}" for i in range(1, total + 1)]


def test_dem_cache_hit_emits_no_tile_lines(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cached raster short-circuits before the tile loop — no phantom progress."""
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(dem_download, "urlopen", _make_fake_urlopen(calls))
    resolve_dem(_BOUNDS, tmp_path)

    lines: list[str] = []
    resolve_dem(_BOUNDS, tmp_path, progress=StageProgress(lines.append))
    assert lines == []


def test_second_call_reuses_cached_raster(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cached raster for the same area is reused — no second WMS request."""
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(dem_download, "urlopen", _make_fake_urlopen(calls))

    first = resolve_dem(_BOUNDS, tmp_path)
    assert len(calls) == 1
    second = resolve_dem(_BOUNDS, tmp_path)
    assert second == first
    assert len(calls) == 1, "second resolve must not re-fetch"


def test_force_refresh_redownloads(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`force_refresh=True` re-fetches even when a cached raster exists."""
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(dem_download, "urlopen", _make_fake_urlopen(calls))

    resolve_dem(_BOUNDS, tmp_path)
    assert len(calls) == 1
    resolve_dem(_BOUNDS, tmp_path, force_refresh=True)
    assert len(calls) == 2


def test_network_failure_maps_to_data_source_unavailable(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A urllib failure surfaces as `DataSourceUnavailableError` (exit-2 tier, NFR6)."""

    def boom(req: Any, timeout: float | None = None) -> _FakeResp:  # noqa: ARG001  # pyright: ignore[reportUnusedParameter]
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(dem_download, "urlopen", boom)

    with pytest.raises(DataSourceUnavailableError) as exc:
        resolve_dem(_BOUNDS, tmp_path)
    assert exc.value.user_message == "DEM source unreachable."


def test_unexpected_payload_size_maps_to_data_source_unavailable(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A short / non-BIL response (e.g. an error document) is rejected, not written."""

    def html_error(req: Any, timeout: float | None = None) -> _FakeResp:  # noqa: ARG001  # pyright: ignore[reportUnusedParameter]
        return _FakeResp(b"<ServiceExceptionReport>boom</ServiceExceptionReport>")

    monkeypatch.setattr(dem_download, "urlopen", html_error)

    with pytest.raises(DataSourceUnavailableError) as exc:
        resolve_dem(_BOUNDS, tmp_path)
    assert exc.value.user_message == "DEM source unreachable."
    # No partial raster left behind.
    assert not (tmp_path / "steeproute" / "dem").exists() or not list(
        (tmp_path / "steeproute" / "dem").glob("*.tif")
    )


def test_right_size_xml_error_document_rejected(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 200-OK XML error doc of coincidentally-correct byte length is rejected by content type."""

    def xml_resp(req: Any, timeout: float | None = None) -> _FakeResp:  # noqa: ARG001  # pyright: ignore[reportUnusedParameter]
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(req.full_url).query)
        width = int(qs["width"][0])
        height = int(qs["height"][0])
        # Exactly the expected BIL byte count, but flagged as an OGC ServiceException.
        return _FakeResp(b"x" * (width * height * 4), content_type="application/vnd.ogc.se_xml")

    monkeypatch.setattr(dem_download, "urlopen", xml_resp)

    with pytest.raises(DataSourceUnavailableError) as exc:
        resolve_dem(_BOUNDS, tmp_path)
    assert exc.value.user_message == "DEM source unreachable."


def _geom_edge_graph(coords_per_edge: list[list[tuple[float, float]]]) -> nx.MultiDiGraph:
    """Build a MultiDiGraph carrying one `LineString` `geometry` per edge (post-stage-4 shape)."""
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    for i, coords in enumerate(coords_per_edge):
        g.add_edge(2 * i, 2 * i + 1, key=0, geometry=shapely.LineString(coords))
    return g


def test_graph_dem_bounds_strictly_contains_every_vertex_with_margin() -> None:
    """Bounds envelope all edge vertices and leave a positive margin on every side.

    The margin is what keeps the extreme vertices strictly interior so
    `sample_elevation`'s half-open (east/north-exclusive) bounds check passes.
    """
    # Two edges spanning a known lon/lat box around the Grenoble fixture latitude.
    graph = _geom_edge_graph(
        [
            [(5.780, 45.250), (5.800, 45.255)],
            [(5.790, 45.245), (5.810, 45.270)],
        ]
    )
    west, south, east, north = graph_dem_bounds(graph)

    min_lon, max_lon = 5.780, 5.810
    min_lat, max_lat = 45.245, 45.270
    # Strictly outside the geometry envelope on all four sides.
    assert west < min_lon and east > max_lon
    assert south < min_lat and north > max_lat
    # Every vertex satisfies sample_elevation's half-open check against these bounds.
    for _u, _v, _k, data in graph.edges(data=True, keys=True):
        for x, y in data["geometry"].coords:
            assert west <= x < east and south < y <= north

    # Margin matches _GRAPH_BOUNDS_MARGIN_M converted to degrees at the box's mid-lat.
    margin_m = dem_download._GRAPH_BOUNDS_MARGIN_M
    mid_lat = (min_lat + max_lat) / 2.0
    exp_lat = margin_m / dem_download._M_PER_DEG_LAT
    exp_lon = margin_m / (dem_download._M_PER_DEG_LAT * math.cos(math.radians(mid_lat)))
    assert math.isclose(min_lat - south, exp_lat, rel_tol=1e-9)
    assert math.isclose(min_lon - west, exp_lon, rel_tol=1e-9)


def test_graph_dem_bounds_rejects_non_linestring_geometry() -> None:
    """A non-LineString edge geometry is an upstream contract violation → TypeError."""
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    g.add_edge(0, 1, key=0, geometry=shapely.Point(5.79, 45.26))
    with pytest.raises(TypeError, match="shapely.LineString"):
        graph_dem_bounds(g)


def test_graph_dem_bounds_empty_graph_raises() -> None:
    """No edge geometry to bound → ValueError (the orchestrator guards against this upstream)."""
    with pytest.raises(ValueError, match="non-empty"):
        graph_dem_bounds(nx.MultiDiGraph())
