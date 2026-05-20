"""Route, Climb, ContractedGraph, and solver-side dataclasses. Implementation lands across Epics 2-3."""

import pathlib
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Area:
    """Geographic search area as a center + bbox half-side.

    `center` is `(lat, lon)` in WGS84 decimal degrees.

    `radius_km` is the **bbox half-side**, not a disk radius. Stage 1 fetches
    OSM with `osmnx.graph_from_point(..., dist_type="bbox")`, which returns
    everything inside a `2 * radius_km`-side square centered on `center`. The
    field is named `radius_km` (rather than `bbox_half_side_km`) to match the
    cache manifest field naming 1:1 (Architecture §Cat 4) and the user-facing
    `--radius` CLI flag — but the geometric meaning is square half-side.

    Lives here (not pipeline/) because the same shape feeds setup-side
    ingestion (Epic 2) and query-side cache coverage check (Epic 3).
    """

    center: tuple[float, float]
    radius_km: float


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    """Knobs for the setup-side pipeline orchestrator (`pipeline.run_setup_stages`).

    Only fields that genuinely change the cached graph live here. `difficulty_cap`
    is intentionally absent: stages 1-7 are parameter-independent over it per
    Architecture §Cat 3b (the cache key omits it; see §Cat 4b), so the
    orchestrator pins it to the most permissive value internally and query-side
    re-filters at the user's chosen cap.

    Smoothing / resample / elevation-median windows stay at their module-scope
    constants in the relevant `pipeline/` modules — no per-call overrides today.
    """

    untagged_policy: str
    dem_path: pathlib.Path
