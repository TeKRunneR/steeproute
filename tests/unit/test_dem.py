# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false, reportMissingParameterType=false, reportArgumentType=false
# Reason: same networkx/shapely/rasterio boundary as pipeline/dem.py;
# rasterio.open(**profile) spreads dynamic kwargs which trips the strict
# argument-type check (sharing/thread_safe expect bool).
"""Unit tests for pipeline.dem: sample_elevation (stage 5)."""

from __future__ import annotations

import math
import pathlib

import networkx as nx
import numpy as np
import pyproj
import pytest
import rasterio
import shapely
from rasterio.transform import from_origin

from steeproute.errors import DEMCoverageError
from steeproute.pipeline.dem import sample_elevation

_FIXTURE_DIR = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "grenoble_small"

# --- helpers -----------------------------------------------------------------


def _write_dem(
    path: pathlib.Path,
    data: np.ndarray,
    transform,
    crs: str,
    nodata: float | None = None,
) -> pathlib.Path:
    """Write a single-band GeoTIFF for use as `sample_elevation`'s `dem_path` arg."""
    profile = {
        "driver": "GTiff",
        "height": data.shape[0],
        "width": data.shape[1],
        "count": 1,
        "dtype": data.dtype.name,
        "crs": crs,
        "transform": transform,
    }
    if nodata is not None:
        profile["nodata"] = nodata
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data, 1)
    return path


def _single_edge_graph(coords: list[tuple[float, float]]) -> nx.MultiDiGraph:
    """Build a one-edge MultiDiGraph carrying the source-attribute contract from Story 2.1."""
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    g.add_node(0, x=coords[0][0], y=coords[0][1])
    g.add_node(1, x=coords[-1][0], y=coords[-1][1])
    g.add_edge(
        0,
        1,
        key=0,
        geometry=shapely.LineString(coords),
        sac_scale="hiking",
        highway="path",
        osm_way_id=12345,
    )
    return g


# --- WGS84 GeoTIFF: attribute contract + axis ordering -----------------------


def test_sample_elevation_adds_vertices_resampled_with_one_entry_per_geometry_coord(
    tmp_path: pathlib.Path,
) -> None:
    """AC #1: per-edge `vertices_resampled` has the same length as `geometry.coords`."""
    # 10x10 WGS84 raster centered near Le Sappey at 0.0001° pixel size (~11 m).
    data = np.arange(100, dtype=np.float32).reshape(10, 10) + 500.0
    transform = from_origin(west=5.788, north=45.261, xsize=0.0001, ysize=0.0001)
    dem_path = _write_dem(tmp_path / "tiny.tif", data, transform, "EPSG:4326")

    # Edge with 3 vertices inside the raster.
    coords = [(5.7885, 45.2605), (5.7887, 45.2603), (5.7889, 45.2601)]
    graph = _single_edge_graph(coords)

    out = sample_elevation(graph, dem_path)

    data_out = out.get_edge_data(0, 1, key=0)
    assert "vertices_resampled" in data_out
    assert len(data_out["vertices_resampled"]) == len(coords)


def test_sample_elevation_vertices_resampled_axis_order_is_lat_lon_elev(
    tmp_path: pathlib.Path,
) -> None:
    """AC #1: tuple ordering is `(lat, lon, elev)` — lat first (opposite shapely)."""
    data = np.full((4, 4), 700.0, dtype=np.float32)
    transform = from_origin(west=5.78, north=45.27, xsize=0.001, ysize=0.001)
    dem_path = _write_dem(tmp_path / "flat.tif", data, transform, "EPSG:4326")

    # Lat ≠ lon (different magnitudes) so we can tell them apart by value.
    coords = [(5.7805, 45.2695), (5.7815, 45.2685)]
    graph = _single_edge_graph(coords)

    out = sample_elevation(graph, dem_path)
    verts = out.get_edge_data(0, 1, key=0)["vertices_resampled"]
    # First tuple entry must be the *lat* (≈ 45.x), not the lon (≈ 5.7x).
    for v in verts:
        lat, lon, elev = v
        assert 45.0 < lat < 46.0, f"first entry should be lat, got {lat}"
        assert 5.0 < lon < 6.0, f"second entry should be lon, got {lon}"
        assert elev == pytest.approx(700.0)


def test_sample_elevation_preserves_attribute_contract(tmp_path: pathlib.Path) -> None:
    """AC #1: `geometry`, `sac_scale`, `highway`, `osm_way_id` carried through unchanged."""
    data = np.full((4, 4), 850.0, dtype=np.float32)
    transform = from_origin(west=5.78, north=45.27, xsize=0.001, ysize=0.001)
    dem_path = _write_dem(tmp_path / "flat.tif", data, transform, "EPSG:4326")

    coords = [(5.7805, 45.2695), (5.7815, 45.2685)]
    graph = _single_edge_graph(coords)
    in_geom = graph.get_edge_data(0, 1, key=0)["geometry"]

    out = sample_elevation(graph, dem_path)
    data_out = out.get_edge_data(0, 1, key=0)

    assert data_out["sac_scale"] == "hiking"
    assert data_out["highway"] == "path"
    assert data_out["osm_way_id"] == 12345
    # geometry preserved (same coords)
    assert list(data_out["geometry"].coords) == list(in_geom.coords)


def test_sample_elevation_does_not_mutate_input(tmp_path: pathlib.Path) -> None:
    """Pure-function discipline: input graph has no `vertices_resampled` after the call."""
    data = np.full((4, 4), 600.0, dtype=np.float32)
    transform = from_origin(west=5.78, north=45.27, xsize=0.001, ysize=0.001)
    dem_path = _write_dem(tmp_path / "flat.tif", data, transform, "EPSG:4326")

    coords = [(5.7805, 45.2695), (5.7815, 45.2685)]
    graph = _single_edge_graph(coords)
    _ = sample_elevation(graph, dem_path)

    data_in = graph.get_edge_data(0, 1, key=0)
    assert "vertices_resampled" not in data_in


def test_sample_elevation_returns_finite_floats(tmp_path: pathlib.Path) -> None:
    """AC #3: no silent NaN — every returned elevation is finite."""
    data = np.array([[100.0, 200.0], [300.0, 400.0]], dtype=np.float32)
    transform = from_origin(west=0.0, north=1.0, xsize=0.5, ysize=0.5)
    dem_path = _write_dem(tmp_path / "small.tif", data, transform, "EPSG:4326")

    coords = [(0.25, 0.75), (0.75, 0.25)]
    graph = _single_edge_graph(coords)

    out = sample_elevation(graph, dem_path)
    for _lat, _lon, elev in out.get_edge_data(0, 1, key=0)["vertices_resampled"]:
        assert math.isfinite(elev)


def test_sample_elevation_empty_graph_is_a_noop(tmp_path: pathlib.Path) -> None:
    """An empty MultiDiGraph passes through cleanly (defensive)."""
    data = np.full((2, 2), 0.0, dtype=np.float32)
    transform = from_origin(west=0.0, north=1.0, xsize=0.5, ysize=0.5)
    dem_path = _write_dem(tmp_path / "void.tif", data, transform, "EPSG:4326")

    graph: nx.MultiDiGraph = nx.MultiDiGraph()
    out = sample_elevation(graph, dem_path)
    assert out.number_of_edges() == 0
    assert out.number_of_nodes() == 0


# --- CRS-aware sampling on non-WGS84 raster ----------------------------------


def test_sample_elevation_transforms_wgs84_coords_to_dem_crs_lambert93(
    tmp_path: pathlib.Path,
) -> None:
    """AC #6: WGS84 graph coords are projected into the DEM's native CRS (Lambert-93) before sampling.

    Construct a 3x3 Lambert-93 (EPSG:2154) raster with a distinctive elevation
    per pixel, place graph vertices in WGS84 that project to known pixels, and
    assert each vertex recovers the seeded elevation. Validates the CRS path
    is exercised even if the production fixture happens to ship in WGS84.
    """
    # 3x3 raster, 5 m pixels, anchored at some Lambert-93 origin near Le Sappey.
    cells = np.array(
        [
            [1000.0, 1010.0, 1020.0],
            [1100.0, 1110.0, 1120.0],
            [1200.0, 1210.0, 1220.0],
        ],
        dtype=np.float32,
    )
    # Center coordinate (Le Sappey) projected to Lambert-93 to anchor the raster
    # — done at runtime so the test does not encode a brittle hand-computed value.
    to_l93 = pyproj.Transformer.from_crs(4326, 2154, always_xy=True).transform
    center_x, center_y = to_l93(5.788, 45.260)
    # Place the raster's top-left so the *center* pixel (col=1, row=1) covers
    # the projected center.
    left = center_x - 7.5  # 1.5 pixels of 5 m to the left
    top = center_y + 7.5
    transform = from_origin(west=left, north=top, xsize=5.0, ysize=5.0)
    dem_path = _write_dem(tmp_path / "lambert93.tif", cells, transform, "EPSG:2154")

    # Graph edge with two vertices in WGS84 that project to known pixel centers:
    # - vertex A → top-left cell (col=0, row=0) → 1000.0
    # - vertex B → center cell    (col=1, row=1) → 1110.0
    to_wgs = pyproj.Transformer.from_crs(2154, 4326, always_xy=True).transform
    a_lon, a_lat = to_wgs(left + 2.5, top - 2.5)
    b_lon, b_lat = to_wgs(left + 7.5, top - 7.5)
    graph = _single_edge_graph([(a_lon, a_lat), (b_lon, b_lat)])

    out = sample_elevation(graph, dem_path)
    verts = out.get_edge_data(0, 1, key=0)["vertices_resampled"]

    assert verts[0][2] == pytest.approx(1000.0)
    assert verts[1][2] == pytest.approx(1110.0)


# --- error paths -------------------------------------------------------------


def test_sample_elevation_raises_dem_coverage_error_for_out_of_bounds_vertex(
    tmp_path: pathlib.Path,
) -> None:
    """AC #3 + AC #7: vertex outside DEM bounds → DEMCoverageError naming edge + bounds."""
    data = np.full((4, 4), 500.0, dtype=np.float32)
    # Tiny raster around (0, 0)
    transform = from_origin(west=0.0, north=1.0, xsize=0.25, ysize=0.25)
    dem_path = _write_dem(tmp_path / "tiny.tif", data, transform, "EPSG:4326")

    # Edge sits far outside the [0, 1] x [0, 1] WGS84 box.
    coords = [(10.0, 10.0), (10.1, 10.1)]
    graph = _single_edge_graph(coords)

    with pytest.raises(DEMCoverageError) as excinfo:
        _ = sample_elevation(graph, dem_path)

    msg = excinfo.value.user_message
    # Must name the offending edge tuple AND the DEM bounds.
    assert "(0, 1, 0)" in msg
    assert "outside DEM bounds" in msg


def test_sample_elevation_raises_dem_coverage_error_for_nodata_cell(
    tmp_path: pathlib.Path,
) -> None:
    """AC #7: vertex landing on a nodata pixel → DEMCoverageError (same path as OOB)."""
    data = np.full((4, 4), 500.0, dtype=np.float32)
    # Mark one cell as nodata.
    data[1, 1] = -9999.0
    transform = from_origin(west=0.0, north=1.0, xsize=0.25, ysize=0.25)
    dem_path = _write_dem(tmp_path / "withnodata.tif", data, transform, "EPSG:4326", nodata=-9999.0)

    # Cell layout with from_origin(west=0, north=1, xsize=0.25, ysize=0.25):
    # data[row, col] covers x ∈ [col*0.25, (col+1)*0.25], y ∈ [1-(row+1)*0.25, 1-row*0.25].
    # data[1, 1] (nodata) covers x ∈ [0.25, 0.5], y ∈ [0.5, 0.75]. Place a vertex there.
    coords = [(0.1, 0.9), (0.375, 0.625)]
    graph = _single_edge_graph(coords)

    with pytest.raises(DEMCoverageError) as excinfo:
        _ = sample_elevation(graph, dem_path)

    assert "nodata or non-finite" in excinfo.value.user_message


def test_sample_elevation_raises_type_error_for_non_linestring_geometry(
    tmp_path: pathlib.Path,
) -> None:
    """Contract guard: non-LineString geometry on an edge is an upstream violation."""
    data = np.full((4, 4), 500.0, dtype=np.float32)
    transform = from_origin(west=0.0, north=1.0, xsize=0.25, ysize=0.25)
    dem_path = _write_dem(tmp_path / "tiny.tif", data, transform, "EPSG:4326")

    g: nx.MultiDiGraph = nx.MultiDiGraph()
    g.add_node(0, x=0.5, y=0.5)
    g.add_node(1, x=0.6, y=0.6)
    g.add_edge(
        0, 1, key=0, geometry=shapely.Point(0.5, 0.5), sac_scale=None, highway="path", osm_way_id=1
    )

    with pytest.raises(TypeError, match="shapely.LineString"):
        _ = sample_elevation(g, dem_path)


def test_sample_elevation_raises_dem_coverage_error_when_dem_has_no_crs(
    tmp_path: pathlib.Path,
) -> None:
    """AC #2 / Review P2: a GeoTIFF without CRS metadata fails fast with DEMCoverageError.

    Without this guard, `pyproj.Transformer.from_crs(WGS84_EPSG, None, ...)` raises
    `pyproj.exceptions.CRSError`, which is not a `SteeprouteError` subclass and would
    leak as an opaque traceback past `run_entry_point`.
    """
    data = np.full((4, 4), 500.0, dtype=np.float32)
    transform = from_origin(west=0.0, north=1.0, xsize=0.25, ysize=0.25)
    # Write a GeoTIFF without a CRS — rasterio accepts crs=None.
    dem_path = tmp_path / "nocrs.tif"
    with rasterio.open(
        dem_path,
        "w",
        driver="GTiff",
        height=data.shape[0],
        width=data.shape[1],
        count=1,
        dtype=data.dtype.name,
        crs=None,
        transform=transform,
    ) as dst:
        dst.write(data, 1)

    coords = [(0.25, 0.75), (0.5, 0.5)]
    graph = _single_edge_graph(coords)

    with pytest.raises(DEMCoverageError, match="no CRS metadata"):
        _ = sample_elevation(graph, dem_path)


def test_sample_elevation_raises_dem_coverage_error_for_nan_nodata(
    tmp_path: pathlib.Path,
) -> None:
    """Review P3: a NaN-nodata raster correctly maps to DEMCoverageError via the isfinite branch.

    When the GeoTIFF declares nodata=NaN and a sampled pixel reads back as NaN,
    `not math.isfinite(elev)` catches it — the explicit equality branch would have
    failed (`NaN == NaN` is False).
    """
    data = np.full((4, 4), 500.0, dtype=np.float32)
    data[1, 1] = float("nan")
    transform = from_origin(west=0.0, north=1.0, xsize=0.25, ysize=0.25)
    dem_path = _write_dem(
        tmp_path / "nan_nodata.tif", data, transform, "EPSG:4326", nodata=float("nan")
    )

    # Cell (col=1, row=1) covers x ∈ [0.25, 0.5], y ∈ [0.5, 0.75] under from_origin.
    coords = [(0.1, 0.9), (0.375, 0.625)]
    graph = _single_edge_graph(coords)

    with pytest.raises(DEMCoverageError, match="nodata or non-finite"):
        _ = sample_elevation(graph, dem_path)


def test_sample_elevation_rejects_vertex_at_exact_east_edge_of_bounds(
    tmp_path: pathlib.Path,
) -> None:
    """Review P1: bounds check is half-open on the east/right edge to match rasterio's pixel grid.

    A point at exactly `bounds.right` maps to pixel column `width` (outside the array).
    With closed-closed bounds the OOB guard would pass, then `dataset.sample` would
    yield a nodata fill that propagates as a bogus elevation. Half-open right catches
    it fail-fast.
    """
    data = np.full((4, 4), 500.0, dtype=np.float32)
    transform = from_origin(west=0.0, north=1.0, xsize=0.25, ysize=0.25)
    dem_path = _write_dem(tmp_path / "edge.tif", data, transform, "EPSG:4326")

    # bounds.right == 1.0 by construction; place a vertex there exactly.
    coords = [(0.5, 0.5), (1.0, 0.5)]
    graph = _single_edge_graph(coords)

    with pytest.raises(DEMCoverageError, match="outside DEM bounds"):
        _ = sample_elevation(graph, dem_path)


# --- real fixture tests (run only if dem.tif is committed) -------------------

_DEM_FIXTURE_PATH = _FIXTURE_DIR / "dem.tif"
_FIXTURE_SIZE_LIMIT_BYTES = 5_000_000  # AC #4 (carries forward Story 2.1's pattern)


@pytest.fixture(scope="module")
def fixture_pipeline_through_stage5() -> nx.MultiDiGraph:
    """Run stages 1→2→3→4→5 against the committed Grenoble fixtures, once per module."""
    import osmnx

    from steeproute.pipeline.osm import normalize_edges
    from steeproute.pipeline.smoothing import resample_edges, smooth_polylines

    osm_path = _FIXTURE_DIR / "osm_graph.graphml"
    if not _DEM_FIXTURE_PATH.exists():
        pytest.skip("dem.tif fixture not yet committed; fixture-driven assertions skipped.")
    graph = normalize_edges(osmnx.load_graphml(osm_path))
    graph = smooth_polylines(graph)
    graph = resample_edges(graph)
    return sample_elevation(graph, _DEM_FIXTURE_PATH)


def _scalar_reference_sample_elevation(
    graph: nx.MultiDiGraph,
    dem_path: pathlib.Path,
) -> nx.MultiDiGraph:
    """Verbatim pre-14.1 per-point scalar `sample_elevation`, kept as the bit-equality oracle.

    This is the exact loop-based algorithm the vectorized `sample_elevation` replaced
    (per-edge `transformer.transform`, per-vertex bounds check, per-point
    `dataset.sample`). Story 14.1's contract is that the vectorized path produces
    **bit-identical** `vertices_resampled` to this reference over every fixture vertex.
    If this ever drifts from production behavior it is only used to prove equality on
    the success path, so a divergence surfaces as a test failure, not a silent skew.
    """
    from steeproute.pipeline.dem import WGS84_EPSG

    out: nx.MultiDiGraph = graph.copy()
    with rasterio.open(dem_path) as dataset:
        dem_crs = dataset.crs
        bounds = dataset.bounds
        nodata = dataset.nodata
        nodata_finite: float | None = (
            float(nodata) if nodata is not None and math.isfinite(float(nodata)) else None
        )
        transformer = pyproj.Transformer.from_crs(WGS84_EPSG, dem_crs, always_xy=True)
        for _u, _v, _k, data in out.edges(data=True, keys=True):
            geom = data["geometry"]
            lons = [float(c[0]) for c in geom.coords]
            lats = [float(c[1]) for c in geom.coords]
            xs, ys = transformer.transform(lons, lats)
            xs_list = [float(x) for x in xs]
            ys_list = [float(y) for y in ys]
            for x, y in zip(xs_list, ys_list, strict=True):
                assert bounds.left <= x < bounds.right and bounds.bottom < y <= bounds.top
            samples = dataset.sample(zip(xs_list, ys_list, strict=True), indexes=1)
            vertices_resampled: list[tuple[float, float, float]] = []
            for lon, lat, sample_arr in zip(lons, lats, samples, strict=True):
                elev = float(sample_arr[0])
                assert math.isfinite(elev) and (nodata_finite is None or elev != nodata_finite)
                vertices_resampled.append((lat, lon, elev))
            data["vertices_resampled"] = vertices_resampled
    return out


def test_fixture_pipeline_bit_identical_to_scalar_reference(
    fixture_pipeline_through_stage5: nx.MultiDiGraph,
) -> None:
    """AC #1: vectorized `sample_elevation` is bit-equal to the old per-point path.

    Runs the same stages-1→4 fixture graph through the scalar reference (the pre-14.1
    algorithm) and asserts every edge's `vertices_resampled` tuple is `==` (exact, not
    `approx`) to the production vectorized output — over every vertex of the real
    Grenoble fixture. This is the "verify before deleting the old code" gate.
    """
    if not _DEM_FIXTURE_PATH.exists():
        pytest.skip("dem.tif fixture not yet committed.")
    import osmnx

    from steeproute.pipeline.osm import normalize_edges
    from steeproute.pipeline.smoothing import resample_edges, smooth_polylines

    graph = normalize_edges(osmnx.load_graphml(_FIXTURE_DIR / "osm_graph.graphml"))
    graph = smooth_polylines(graph)
    graph = resample_edges(graph)
    reference = _scalar_reference_sample_elevation(graph, _DEM_FIXTURE_PATH)

    produced = fixture_pipeline_through_stage5
    ref_edges = {
        (u, v, k): d["vertices_resampled"] for u, v, k, d in reference.edges(keys=True, data=True)
    }
    prod_edges = {
        (u, v, k): d["vertices_resampled"] for u, v, k, d in produced.edges(keys=True, data=True)
    }
    assert prod_edges.keys() == ref_edges.keys(), "edge set diverged from the scalar reference"
    total_vertices = 0
    for edge_key, ref_verts in ref_edges.items():
        prod_verts = prod_edges[edge_key]
        assert prod_verts == ref_verts, f"vertices_resampled diverged on edge {edge_key}"
        total_vertices += len(ref_verts)
    # Guard against a vacuous pass: the fixture must actually carry vertices.
    assert total_vertices > 1000, f"expected a substantial fixture, sampled only {total_vertices}"


def test_committed_dem_fixture_under_size_cap() -> None:
    """AC #4: committed dem.tif must stay under 5 MB to keep the repo lean."""
    if not _DEM_FIXTURE_PATH.exists():
        pytest.skip("dem.tif fixture not yet committed.")
    size = _DEM_FIXTURE_PATH.stat().st_size
    assert size < _FIXTURE_SIZE_LIMIT_BYTES, (
        f"Fixture {_DEM_FIXTURE_PATH.name} is {size} bytes, exceeds {_FIXTURE_SIZE_LIMIT_BYTES}."
    )


# --- inverted-bounds sanity check (Story 2.8 carry-forward, deferred D2 from 2.3) ---


def test_sample_elevation_rejects_flipped_origin_dem(tmp_path: pathlib.Path) -> None:
    """A DEM whose transform flips N/S origin surfaces a clear `inverted ... bounds` error.

    Without this guard every vertex fails the per-vertex OOB check, hiding the
    underlying "this raster is upside-down" diagnosis behind a wall of unhelpful
    DEMCoverageErrors.
    """
    data = np.full((10, 10), 500.0, dtype=np.float32)
    # `ysize=-0.0001` flips origin: north < south, top < bottom on `dataset.bounds`.
    transform = from_origin(west=5.788, north=45.260, xsize=0.0001, ysize=-0.0001)
    dem_path = _write_dem(tmp_path / "flipped.tif", data, transform, "EPSG:4326")
    graph = _single_edge_graph([(5.788, 45.260), (5.7882, 45.2602)])

    with pytest.raises(DEMCoverageError, match=r"inverted or zero-width bounds"):
        _ = sample_elevation(graph, dem_path)


def test_fixture_pipeline_preserves_attribute_contract(
    fixture_pipeline_through_stage5: nx.MultiDiGraph,
) -> None:
    """AC #5: every edge has the full attribute contract after stage 5."""
    for _u, _v, _k, data in fixture_pipeline_through_stage5.edges(data=True, keys=True):
        assert isinstance(data["geometry"], shapely.LineString)
        assert "sac_scale" in data
        assert "highway" in data
        assert "osm_way_id" in data
        assert isinstance(data["vertices_resampled"], list)
        # One vertex per geometry coord.
        assert len(data["vertices_resampled"]) == len(list(data["geometry"].coords))


def test_fixture_pipeline_vertices_resampled_matches_geometry_coords(
    fixture_pipeline_through_stage5: nx.MultiDiGraph,
) -> None:
    """AC #5: vertices_resampled (lat, lon) match the geometry (lon, lat) per edge."""
    for _u, _v, _k, data in fixture_pipeline_through_stage5.edges(data=True, keys=True):
        geom_coords = list(data["geometry"].coords)
        verts = data["vertices_resampled"]
        for (lon, lat), (vlat, vlon, _elev) in zip(geom_coords, verts, strict=True):
            assert vlat == pytest.approx(lat, abs=1e-9)
            assert vlon == pytest.approx(lon, abs=1e-9)


def test_fixture_pipeline_elevations_are_finite_and_plausible(
    fixture_pipeline_through_stage5: nx.MultiDiGraph,
) -> None:
    """AC #5: Chartreuse Massif elevations land in a sane Alpine band (300 m ≤ elev ≤ 2000 m).

    Le Sappey-en-Chartreuse bbox spans ~370–1620 m per the DEM source; widening
    the band a touch keeps the assertion robust against minor IGN data updates.
    """
    for _u, _v, _k, data in fixture_pipeline_through_stage5.edges(data=True, keys=True):
        for _lat, _lon, elev in data["vertices_resampled"]:
            assert math.isfinite(elev)
            assert 300.0 <= elev <= 2000.0, f"Implausible Alpine elevation: {elev} m"


@pytest.mark.parametrize(
    ("landmark", "lat", "lon", "ref_m", "tol_m"),
    [
        # Le Sappey-en-Chartreuse village center. IGN reference altitude for the
        # commune is 1004 m (commune mairie altitude per insee.fr); our sampled
        # 5 m DEM cell covering (45.260, 5.788) lands at ≈1023 m. Tolerance ±50 m
        # absorbs both the cell-vs-point ambiguity at 5 m resolution and any
        # minor reference-altitude drift between sources.
        ("Le Sappey village", 45.260, 5.788, 1004.0, 50.0),
        # Bbox SE corner — drops into the Isère valley toward Grenoble (~460 m).
        # Picked to give a known-low anchor that's ~600 m below the village,
        # which a wrong-CRS or lat/lon-axis-swap bug would flunk loudly.
        ("Isère valley edge", 45.245, 5.806, 460.0, 50.0),
    ],
)
def test_sample_elevation_at_known_landmark_within_tolerance(
    landmark: str,
    lat: float,
    lon: float,
    ref_m: float,
    tol_m: float,
) -> None:
    """AC #5: known-elevation cross-check against external references at named landmarks.

    Uses the committed `dem.tif` directly (not the chained pipeline) so a failure
    here pinpoints DEM-sampling correctness independent of stages 3-4.
    """
    if not _DEM_FIXTURE_PATH.exists():
        pytest.skip("dem.tif fixture not yet committed.")
    # Single-vertex synthetic edge at the landmark. The polyline needs ≥ 2
    # distinct points to be valid for shapely, so we offset the second point by
    # a tiny amount; the assertion targets the first vertex.
    coords = [(lon, lat), (lon + 1e-5, lat + 1e-5)]
    graph = _single_edge_graph(coords)
    out = sample_elevation(graph, _DEM_FIXTURE_PATH)
    verts = out.get_edge_data(0, 1, key=0)["vertices_resampled"]
    elev = verts[0][2]
    assert abs(elev - ref_m) <= tol_m, (
        f"{landmark}: sampled {elev:.1f} m at ({lat}, {lon}) "
        f"is {abs(elev - ref_m):.1f} m from reference {ref_m} m (tolerance {tol_m} m)."
    )
