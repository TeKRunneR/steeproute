# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
# Reason: `check_coverage` returns `PreparedData` whose `graph` is a
# `MultiDiGraph[Unknown]` upstream (networkx generic parameter unspecified).
# Same external-boundary pattern as `cli/setup.py` and `pipeline/`.
"""steeproute query CLI entry point: FR24 coverage check → stages 8-9 → GRASP → validate → render.

Story 2.10 wired the cache-hit path through `cache.check_coverage`, which
resolves the user's `--center` / `--radius` against `index.json` and either
returns the smallest-radius `PreparedData` strictly containing the query area
or raises `CacheNotFoundError` (mapped to exit 2 by `run_entry_point`).

Story 3.11 wires the full Journey-1 happy path on top of that: climb detection
(stage 8) → contracted-graph construction (stage 9) → GRASP → runtime validation
→ HTML/JSON rendering. The process exit code is validation-driven (§Cat 6c):
`0` when every route passes, `1` when any route fails validation or any
set-level pairwise distinctness violation exists. Outputs are always written to
disk *before* the exit code is computed, so disk state is correct regardless of
exit code (FR28). Progress UI + interrupt handling (real `progress_callback`,
Ctrl-C → exit 130) land in Epic 4; this CLI passes a no-op callback (`None`).
"""

from __future__ import annotations

import datetime
import pathlib
from typing import NoReturn

import click
import numpy as np

from steeproute import output
from steeproute.cache import Manifest, check_coverage, resolve_cache_root
from steeproute.cli._shared import (
    area_cap_option,
    cache_dir_option,
    center_option,
    configure_cli_logging,
    difficulty_cap_option,
    emit_osm_age_warning,
    ensure_output_dir,
    iter_budget_option,
    j_max_option,
    l_connector_option,
    min_climb_ground_length_option,
    n_option,
    osm_age_warn_days_option,
    output_dir_option,
    progress_interval_option,
    quiet_option,
    radius_option,
    run_entry_point,
    seed_option,
    stagnation_iters_option,
    theta_option,
    time_budget_option,
    untagged_trails_option,
    validate_area_size,
    validate_solver_options,
    verbose_option,
)
from steeproute.models import Area, ProvenanceInfo, SolverParams, ValidatedRouteSet
from steeproute.pipeline.climbs import detect_climbs
from steeproute.pipeline.graph import contract_climbs
from steeproute.solver.grasp import GraspSolver
from steeproute.validator import validate

# Concrete fallback when `--iter-budget` is unset. Epic 3's GRASP terminates on
# iter-budget only (time-budget + stagnation land in Epic 4), so the CLI must
# resolve a positive integer here. Sized to find routes on a real Grenoble query
# while staying well inside NFR1's 10-minute design target; tunable post-baseline
# once Epic 4 wires the time/stagnation termination that would normally cap it.
DEFAULT_ITER_BUDGET: int = 2000

# Fixed convergence status for Epic 3: the solver runs to its iteration budget,
# which maps to "budget-exhausted" in the §Cat 5e termination table. Story 4.2
# replaces this with the full three-value contract (converged / budget-exhausted
# / interrupted) once stagnation + interrupt handling exist.
_CONVERGENCE_STATUS: output.ConvergenceStatus = "budget-exhausted"


@click.command(
    name="steeproute",
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.version_option(package_name="steeproute", prog_name="steeproute")
@center_option
@radius_option
@theta_option
@difficulty_cap_option
@l_connector_option
@min_climb_ground_length_option
@j_max_option
@n_option
@area_cap_option
@untagged_trails_option
@seed_option
@iter_budget_option
@time_budget_option
@stagnation_iters_option
@progress_interval_option
@output_dir_option
@verbose_option
@quiet_option
@cache_dir_option
@osm_age_warn_days_option
def cli(
    *,
    center: tuple[float, float],
    radius: float,
    theta: float,
    difficulty_cap: str,
    l_connector: float,
    min_climb_ground_length: float,
    j_max: float,
    n: int,
    area_cap: float,
    untagged_trails: str,
    seed: int | None,
    iter_budget: int | None,
    time_budget: float,
    stagnation_iters: int | None,
    progress_interval: float | None,
    output_dir: pathlib.Path,
    verbose: bool,
    quiet: bool,
    cache_dir: pathlib.Path | None,
    osm_age_warn_days: int,
) -> int:
    configure_cli_logging(verbose=verbose)

    # FR2 sanity: reject queries whose disk-area exceeds --area-cap before we
    # walk the cache. A typo like `--radius 5000` should fail-fast at the CLI
    # boundary, not after a successful cache walk.
    validate_area_size(radius_km=radius, area_cap_km2=area_cap)

    # Solver-parameter sanity at the CLI boundary (§Cat 10 → exit 2). Out-of-range
    # values would otherwise surface as a raw `ValueError` traceback from
    # `GraspSolver`/`TopNTracker`, and a `nan` slope floor would silently yield
    # zero routes. Fail-fast here, before the cache walk and the solve.
    validate_solver_options(
        theta=theta,
        l_connector=l_connector,
        min_climb_ground_length=min_climb_ground_length,
        j_max=j_max,
        n=n,
        iter_budget=iter_budget,
    )
    # Create the output directory now so an unusable `--output-dir` fails as a
    # clean exit 2 rather than an `OSError` traceback mid-render.
    ensure_output_dir(output_dir)

    area = Area(center=center, radius_km=radius)
    cache_root = resolve_cache_root(cache_dir)

    # FR24 coverage check. Raises `CacheNotFoundError` (→ exit 2 via
    # `run_entry_point`) when no prepared cache strictly contains the query
    # area; opportunistically rebuilds `index.json` if a prior `write_entry`
    # was interrupted before its final rebuild call.
    prepared = check_coverage(cache_root, area)

    # OSM-age warning on cache-hit (Architecture §Cat 4f). The query CLI has no
    # `--force-refresh` flag of its own — the helper's shared message tells the
    # user to re-run `steeproute-setup --force-refresh` for this area.
    emit_osm_age_warning(
        manifest=prepared.manifest,
        threshold_days=osm_age_warn_days,
        now=datetime.datetime.now(datetime.UTC),
    )

    # Cache-hit cue on stdout (kept from Story 2.10 — the full run summary lands
    # in Epic 4 Story 4.5). Single space between tokens for downstream tooling
    # that splits on whitespace.
    print(f"steeproute: cache-hit cache_key_hash: {prepared.manifest.cache_key_hash}")

    # --- Journey 1 happy path: stages 8-9 → GRASP → validate → render --------
    params = SolverParams(
        theta=theta,
        difficulty_cap=difficulty_cap,
        l_connector=l_connector,
        min_climb_ground_length=min_climb_ground_length,
        j_max=j_max,
        n=n,
        area_cap=area_cap,
        untagged_policy=untagged_trails,
        seed=seed,
        # Epic 3 terminates on iter-budget only; resolve the `None` default to a
        # concrete positive count (Epic 4 adds time/stagnation termination).
        iter_budget=iter_budget if iter_budget is not None else DEFAULT_ITER_BUDGET,
        time_budget=time_budget,
        # `None` (flag unset) → 0 disables stagnation termination (§Cat 5e); the
        # real default is tuned in Epic 4 Story 4.2.
        stagnation_iters=stagnation_iters if stagnation_iters is not None else 0,
    )
    provenance = _build_provenance(prepared.manifest)

    climbs = detect_climbs(
        prepared.graph,
        theta=theta,
        min_climb_ground_length=min_climb_ground_length,
    )
    contracted = contract_climbs(prepared.graph, climbs, l_connector=l_connector)

    # No-op progress callback for Epic 3 (Story 4.1 wires the throttled renderer);
    # seed threads straight into the RNG so `--seed` produces byte-identical
    # edge-sets (FR29). An unseeded run passes `None` → non-deterministic.
    solver = GraspSolver(contracted, params, np.random.default_rng(seed), progress_callback=None)
    solutions = solver.run()

    validated = validate(solutions, contracted, params)

    # Render every route (failed ones too, with a banner — FR28) BEFORE computing
    # the exit code, so disk state is identical regardless of pass/fail (§Cat 6c).
    output.render(
        validated,
        prepared.graph,
        contracted,
        params,
        provenance,
        _CONVERGENCE_STATUS,
        output_dir,
    )

    # Acknowledge the Epic-4 kwargs (progress UI) so basedpyright doesn't flag them.
    _ = (progress_interval, quiet)

    # Exit-code coupling (§Cat 6c / FR28 / FR30): 1 if any route failed validation
    # OR any set-level pairwise violation exists; 0 otherwise. `ctx.exit(code)`
    # raises SystemExit, which `_invoke_command` maps to the process exit code —
    # returning the int from this callback would be discarded by click's
    # standalone mode (it always exits 0 on a plain return).
    click.get_current_context().exit(_exit_code_for(validated))


def _build_provenance(manifest: Manifest) -> ProvenanceInfo:
    """Build the report's `ProvenanceInfo` from the cache entry that fed this query.

    The four cache-derived fields echo the manifest verbatim (the report
    describes the *prepared data* it was generated from — §Cat 4b/§Cat 9). The
    git commit is split out of `manifest.steeproute_commit`, which `provenance.
    get_commit_short()` produced with a `-dirty` suffix when the setup-time tree
    was modified; `ProvenanceInfo` carries the short hash and the dirty flag as
    separate fields so the renderer can re-compose `<hash>-dirty` consistently.
    """
    commit = manifest.steeproute_commit
    git_dirty = commit.endswith("-dirty")
    git_commit_short = commit[: -len("-dirty")] if git_dirty else commit
    return ProvenanceInfo(
        steeproute_version=manifest.steeproute_version,
        git_commit_short=git_commit_short,
        git_dirty=git_dirty,
        osm_extract_date=manifest.osm_extract_date,
        dem_version=manifest.dem_version,
        pipeline_content_hash=manifest.pipeline_content_hash,
    )


def _exit_code_for(validated: ValidatedRouteSet) -> int:
    """Validation-driven exit code (§Cat 6c): 1 on any failure, else 0."""
    any_per_route_failure = any(not r.validation.passed for r in validated.routes)
    any_pairwise_failure = bool(validated.set_violations)
    return 1 if (any_per_route_failure or any_pairwise_failure) else 0


def _invoke_command() -> int:
    """Invoke the click command in standalone mode and convert its SystemExit into an int."""
    try:
        cli.main(standalone_mode=True)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 0
    return 0


def main() -> NoReturn:
    run_entry_point(_invoke_command)
