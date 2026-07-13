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
exit code (FR28). Story 7.1 wires the progress UI: a throttled `ProgressEvent`
renderer is installed on the solver (suppressed by `--quiet`). Story 7.3 adds
Ctrl-C interrupt handling (best-so-far flush → exit 130); Story 7.5 prints the
end-of-run summary to stdout (FR22).

The non-solver query phases run inside the same `StageProgress` seam the setup
CLI uses (Story 11.1 mechanism): cache load, elevation reshaping (stages 6-7),
trail-filter redux, climb detection/contraction, and validate+render each
announce start and elapsed time on stdout, so on large areas — where these
phases dominate wall-clock (Epic 13) — the run is never silent outside the
solve. `--quiet` installs no sink, suppressing stage lines like it does
progress lines (§Cat 8: summary always prints).
"""

from __future__ import annotations

import datetime
import pathlib
import time
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
    elevation_deadband_option,
    elevation_smoothing_option,
    emit_osm_age_warning,
    ensure_output_dir,
    iter_budget_option,
    j_max_option,
    l_connector_option,
    max_descent_slope_option,
    merge_interval_option,
    min_climb_ground_length_option,
    min_climb_slope_option,
    n_option,
    osm_age_warn_days_option,
    output_dir_option,
    progress_interval_option,
    quiet_option,
    radius_option,
    run_entry_point,
    seed_option,
    stagnation_iters_option,
    start_at_junction_option,
    theta_option,
    time_budget_option,
    untagged_trails_option,
    validate_area_size,
    validate_solver_options,
    verbose_option,
    workers_option,
)
from steeproute.models import (
    Area,
    ContractedGraph,
    ConvergenceStatus,
    ProvenanceInfo,
    Solution,
    SolverParams,
    ValidatedRouteSet,
)
from steeproute.pipeline import operationalize_graph
from steeproute.pipeline.climbs import detect_climbs
from steeproute.pipeline.graph import contract_climbs
from steeproute.pipeline.osm import filter_trails
from steeproute.progress import ProgressCallback, ProgressEvent, StageProgress, throttle
from steeproute.solver.grasp import STAGNATION_ITERS_DEFAULT_PLACEHOLDER, GraspSolver
from steeproute.solver.parallel import (
    ParallelGraspFailed,
    ParallelGraspInterrupted,
    ParallelProgress,
    run_parallel_grasp,
)
from steeproute.validator import validate

# Concrete fallback when `--iter-budget` is unset: the iteration ceiling that
# bounds a solve once neither `--time-budget` nor `--stagnation-iters` has fired
# first (§Cat 5e). Sized to find routes on a real Grenoble query while staying
# well inside NFR1's 10-minute design target; tunable post-baseline.
DEFAULT_ITER_BUDGET: int = 2000


@click.command(
    name="steeproute",
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.version_option(package_name="steeproute", prog_name="steeproute")
@center_option
@radius_option
@theta_option
@min_climb_slope_option
@difficulty_cap_option
@l_connector_option
@min_climb_ground_length_option
@elevation_smoothing_option
@elevation_deadband_option
@j_max_option
@start_at_junction_option
@max_descent_slope_option
@n_option
@area_cap_option
@untagged_trails_option
@seed_option
@iter_budget_option
@time_budget_option
@stagnation_iters_option
@workers_option
@merge_interval_option
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
    min_climb_slope: float,
    difficulty_cap: str,
    l_connector: float,
    min_climb_ground_length: float,
    elevation_smoothing: float,
    elevation_deadband: float,
    j_max: float,
    start_at_junction: bool,
    max_descent_slope: float | None,
    n: int,
    area_cap: float,
    untagged_trails: str,
    seed: int | None,
    iter_budget: int | None,
    time_budget: float,
    stagnation_iters: int | None,
    workers: int,
    merge_interval: int,
    progress_interval: float,
    output_dir: pathlib.Path,
    verbose: bool,
    quiet: bool,
    cache_dir: pathlib.Path | None,
    osm_age_warn_days: int,
) -> int:
    configure_cli_logging(verbose=verbose)

    # Whole-invocation wall-clock start (Story 7.5, FR22): spans the coverage
    # check, stages 8-9, the solve, validation, and render — the elapsed reported
    # in the end-of-run summary. `perf_counter` (monotonic) mirrors `cli/setup.py`.
    start = time.perf_counter()

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
        min_climb_slope=min_climb_slope,
        l_connector=l_connector,
        min_climb_ground_length=min_climb_ground_length,
        elevation_smoothing=elevation_smoothing,
        elevation_deadband=elevation_deadband,
        j_max=j_max,
        n=n,
        iter_budget=iter_budget,
        time_budget=time_budget,
        stagnation_iters=stagnation_iters,
        progress_interval=progress_interval,
        workers=workers,
        merge_interval=merge_interval,
        max_descent_slope=max_descent_slope,
    )
    # Create the output directory now so an unusable `--output-dir` fails as a
    # clean exit 2 rather than an `OSError` traceback mid-render.
    ensure_output_dir(output_dir)

    area = Area(center=center, radius_km=radius)
    cache_root = resolve_cache_root(cache_dir)

    # Stage-timing seam for the non-solver query phases (Story 11.1 mechanism,
    # reused query-side): on large areas these phases dominate wall-clock
    # (Epic 13), so each announces itself and its elapsed time on stdout. Same
    # `--quiet` contract as the setup CLI — no sink, timing-only no-op. The
    # solver's own iteration progress (Story 7.1) is separate and untouched.
    stage_progress = StageProgress(on_line=None if quiet else print)

    # FR24 coverage check. Raises `CacheNotFoundError` (→ exit 2 via
    # `run_entry_point`) when no prepared cache strictly contains the query
    # area; opportunistically rebuilds `index.json` if a prior `write_entry`
    # was interrupted before its final rebuild call. The stage line covers the
    # index walk plus the entry deserialization (`read_entry` graph rebuild).
    with stage_progress.stage("load-prepared-area"):
        prepared = check_coverage(cache_root, area)

    # OSM-age warning on cache-hit (Architecture §Cat 4f). The query CLI has no
    # `--force-refresh` flag of its own — the helper's shared message tells the
    # user to re-run `steeproute-setup --force-refresh` for this area.
    emit_osm_age_warning(
        manifest=prepared.manifest,
        threshold_days=osm_age_warn_days,
        now=datetime.datetime.now(datetime.UTC),
    )

    # Cache-hit cue on stdout (kept from Story 2.10; the end-of-run summary that
    # Story 7.5 adds is a separate block). Single space between tokens for
    # downstream tooling that splits on whitespace.
    print(f"steeproute: cache-hit cache_key_hash: {prepared.manifest.cache_key_hash}")

    # --- Journey 1 happy path: stages 8-9 → GRASP → validate → render --------
    params = SolverParams(
        theta=theta,
        min_climb_slope=min_climb_slope,
        difficulty_cap=difficulty_cap,
        l_connector=l_connector,
        min_climb_ground_length=min_climb_ground_length,
        j_max=j_max,
        start_at_junction=start_at_junction,
        max_descent_slope=max_descent_slope,
        n=n,
        area_cap=area_cap,
        untagged_policy=untagged_trails,
        seed=seed,
        # Resolve the `None` flag default to a concrete iteration ceiling; with
        # `--time-budget` and `--stagnation-iters` now live (§Cat 5e), whichever
        # of the three trips first ends the solve.
        iter_budget=iter_budget if iter_budget is not None else DEFAULT_ITER_BUDGET,
        time_budget=time_budget,
        # `None` (flag unset) → the solver's provisional default window; pass `0`
        # explicitly to disable stagnation termination (§Cat 5e).
        stagnation_iters=(
            stagnation_iters
            if stagnation_iters is not None
            else STAGNATION_ITERS_DEFAULT_PLACEHOLDER
        ),
    )
    provenance = _build_provenance(prepared.manifest)

    # Query-side stages 6-7 (Story 6.3): reshape the cached raw-elevation graph
    # into the operational graph (graph-Laplacian smoothing → deadband → naive-sum
    # metrics) ONCE over the whole graph. The same reshaped graph feeds both the
    # metric/solver path and `output.render`, so the metric box, the solver
    # objective, and the plotted curve all read one canonical profile (box==curve).
    with stage_progress.stage("elevation-reshape", note="stages 6-7"):
        # `consume=True`: reshape the cache-loaded graph in place, skipping one
        # full-graph copy (~5 s on an r20 graph). Safe because `prepared.graph` is
        # freshly loaded here and never read again after this call — only
        # `operational_graph` (its reshaped self) and `prepared.manifest` are used
        # downstream. Output is identical to the copying path.
        operational_graph = operationalize_graph(
            prepared.graph,
            elevation_smoothing_m=elevation_smoothing,
            elevation_deadband_m=elevation_deadband,
            consume=True,
        )

    # SAC cap-aware contraction (Story 6.1, FR4/FR10): drop above-cap edges
    # *before* climb detection so a single over-cap pitch can no longer weld
    # itself into an otherwise-usable climb (the max-rank SAC aggregation in
    # `contract_climbs` would otherwise reject the whole climb at the RCL). The
    # query-side cap keeps the prepared cache difficulty-independent (setup pins
    # T6; the cache key omits `difficulty_cap`), so `--difficulty-cap` stays a
    # fast query knob. `filter_trails` re-applies the trail-highway + untagged
    # filters too — idempotent on the already-setup-filtered graph — and never
    # mutates its input. The filtered graph feeds detection and contraction;
    # `output.render` keeps the full `operational_graph` for geometry lookups
    # (read-only, strictly a superset — so FR28 failed-route rendering can never
    # lose a route edge's geometry, and the rendered curve matches the box).
    with stage_progress.stage("trail-filter", note="difficulty-cap redux"):
        routable_graph = filter_trails(operational_graph, untagged_trails, difficulty_cap)

    # Progress UI (Story 7.1, FR13): install a throttled stdout renderer unless
    # `--quiet`. The throttle is a pure reporting side-effect — `seed` threads
    # straight into the RNG, so `--seed` produces byte-identical edge-sets (FR29)
    # regardless of whether progress fires. An unseeded run passes `None` seed →
    # non-deterministic by design.
    progress_callback: ProgressCallback | None = (
        None if quiet else throttle(_render_progress, progress_interval)
    )

    def _validate_and_render(
        route_set: list[Solution],
        status: ConvergenceStatus,
        contracted_graph: ContractedGraph,
        convergence_iteration: int,
    ) -> tuple[ValidatedRouteSet, str | None]:
        """Validate `route_set`, render every route (failed ones too — FR28), return both.

        Single-sources the validate → render pair shared by the normal and the
        Ctrl-C paths so the interrupted output cannot drift from a normal run (one
        9-argument `output.render` call shape, one place to change). The varying
        bits (`route_set`, `status`, `contracted_graph`, `convergence_iteration`)
        are passed in; the run-wide context is captured.

        Returns the validated set plus the graceful-degradation message (FR12) that
        was embedded in the reports, or `None` — so the caller's stdout print uses
        the exact same string, computed once. An interrupted partial set is
        explained by `convergence_status` instead, so the message is suppressed
        there (a short run isn't a sparse area).
        """
        with stage_progress.stage("validate-render"):
            validated_set = validate(route_set, contracted_graph, params)
            degradation = (
                None if status == "interrupted" else _degradation_message(validated_set, params)
            )
            output.render(
                validated_set,
                operational_graph,
                area,
                contracted_graph,
                params,
                provenance,
                status,
                convergence_iteration,
                output_dir,
                degradation=degradation,
            )
        return validated_set, degradation

    # Interrupt handling (Story 7.3, FR14 / NFR3 / §Cat 5b): Ctrl-C anywhere in the
    # detect → contract → solve region flushes the solver's best-so-far top-N to
    # disk and exits 130. The solver is built lazily inside the try, and both it
    # and `contracted` start `None`, so an interrupt during stages 8-9 (before the
    # solver exists) still lands in the handler and is told apart from a partial
    # solve. The interrupt is caught HERE rather than left to propagate: click's
    # standalone mode would otherwise print "Aborted!" and exit 1, masking the
    # dedicated interrupt code — so we render the partial set and signal 130 via
    # `ctx.exit`, which `_invoke_command`'s `SystemExit` capture forwards verbatim.
    contracted: ContractedGraph | None = None
    solver: GraspSolver | None = None
    try:
        with stage_progress.stage("climb-detection"):
            climbs = detect_climbs(
                routable_graph,
                min_climb_slope=min_climb_slope,
                min_climb_ground_length=min_climb_ground_length,
            )
        with stage_progress.stage("climb-contraction"):
            contracted = contract_climbs(
                routable_graph,
                climbs,
                l_connector=l_connector,
                annotate_junctions=params.start_at_junction,
            )
        if workers == 1:
            # Single-process path — byte-identical to pre-14.4 (FR29/NFR4). The
            # parallel machinery below is never entered at the default `--workers 1`,
            # so goldens and Story 7.3's live-best-so-far interrupt flush are
            # preserved bit-for-bit.
            solver = GraspSolver(
                contracted, params, np.random.default_rng(seed), progress_callback=progress_callback
            )
            solutions = solver.run()
            status = solver.convergence_status
            convergence_iteration = solver.convergence_iteration
        else:
            # Parallel GRASP restarts (Story 14.4): fan across `workers` processes
            # with island-model elite migration every `--merge-interval` iterations,
            # merging into one top-N. Deterministic per `(seed, workers,
            # merge_interval)`, but differs from `--workers 1` by design (independent
            # seed streams + partitioned budget). Purely CLI-layer orchestration — no
            # `SolverParams`/cache impact. Live aggregate progress crosses back via a
            # queue (suppressed by `--quiet`).
            try:
                parallel = run_parallel_grasp(
                    contracted,
                    params,
                    seed,
                    workers,
                    merge_interval=merge_interval,
                    progress_interval=progress_interval,
                    on_progress=None if quiet else _render_parallel_progress,
                )
                solutions = parallel.solutions
                status = parallel.convergence_status
                convergence_iteration = parallel.convergence_iteration
            except ParallelGraspFailed as exc:
                # A worker died (typically OOM — each worker holds its own graph copy,
                # so memory grows O(workers)). Fall back to a correct single-process
                # solve rather than crash; `solver` is assigned so a Ctrl-C during the
                # fallback still lands in the interrupt handler below.
                click.echo(
                    f"warning: parallel solve failed ({exc}); falling back to "
                    f"--workers 1. Try fewer --workers.",
                    err=True,
                )
                solver = GraspSolver(
                    contracted,
                    params,
                    np.random.default_rng(seed),
                    progress_callback=progress_callback,
                )
                solutions = solver.run()
                status = solver.convergence_status
                convergence_iteration = solver.convergence_iteration
    except ParallelGraspInterrupted as interrupt:
        # N>1 Ctrl-C (§Cat 5b): render the top-N salvaged from workers that had
        # already returned, tagged `interrupted`, and exit 130. In-flight workers'
        # partial best-so-far can't be recovered across the process boundary — a
        # documented degradation from the single-process flush below.
        ctx = click.get_current_context()
        partial = interrupt.partial
        if contracted is None or not partial.solutions:
            click.echo("interrupted before any solution found", err=True)
            ctx.exit(130)
        _validate_and_render(
            partial.solutions, "interrupted", contracted, partial.convergence_iteration
        )
        ctx.exit(130)
    except KeyboardInterrupt:
        ctx = click.get_current_context()
        if solver is None or contracted is None or not solver.best_so_far:
            # Interrupted before any route was admitted — nothing to render. Warn
            # on stderr (§Cat 8) and exit with the dedicated interrupt code.
            click.echo("interrupted before any solution found", err=True)
            ctx.exit(130)
        # Flush the partial best-so-far through the same validate → render path as a
        # normal run, tagged `interrupted` (§Cat 5e) with the iteration the last
        # improvement landed on. Set the status on the solver too, so a later reader
        # of `solver.convergence_status` (e.g. Story 7.5's run summary) agrees with
        # the rendered report. A single Ctrl-C writes the partial set before exiting
        # (FR28); a rare second Ctrl-C during render can truncate the set, but the
        # per-file atomic writes keep every emitted file and the cache valid (NFR3).
        solver.convergence_status = "interrupted"
        _validate_and_render(
            solver.best_so_far, "interrupted", contracted, solver.convergence_iteration
        )
        ctx.exit(130)

    # §Cat 5e: the termination that fired (`converged` on stagnation,
    # `budget-exhausted` on iter/time budget; `interrupted` on the Ctrl-C paths
    # above). For N>1 this is the aggregated status from `run_parallel_grasp`.
    validated, degradation = _validate_and_render(
        solutions, status, contracted, convergence_iteration
    )

    # End-of-run summary on stdout (Story 7.5, FR22): printed after render on the
    # normal path, before the exit-code call, so it always appears regardless of the
    # validation outcome. Always stdout — `--quiet` only gates intermediate progress
    # (§Cat 8). It absorbs the graceful-degradation explanation (FR12) into its
    # `degradation:` field (the same string embedded in each report, computed once in
    # `_validate_and_render`); degradation is a normal outcome (§Cat 6c) and never
    # changes the exit code below.
    print(
        _run_summary(
            validated,
            params,
            status,
            workers,
            merge_interval,
            sum(s.objective for s in solutions),
            time.perf_counter() - start,
            degradation,
        )
    )

    # Exit-code coupling (§Cat 6c / FR28 / FR30): 1 if any route failed validation
    # OR any set-level pairwise violation exists; 0 otherwise. `ctx.exit(code)`
    # raises SystemExit, which `_invoke_command` maps to the process exit code —
    # returning the int from this callback would be discarded by click's
    # standalone mode (it always exits 0 on a plain return).
    click.get_current_context().exit(_exit_code_for(validated))


def _render_progress(event: ProgressEvent) -> None:
    """Format one `ProgressEvent` as a single stdout line (Architecture §Cat 8).

    Progress goes through `print()` to stdout — never `logging` (which §Cat 8
    binds to stderr for diagnostics/warnings). The `progress:` prefix is a stable
    sentinel: the run summary (Story 7.5) uses its own delimiter, so downstream
    tooling and the e2e quiet test can distinguish progress lines unambiguously.
    `eta=?` marks an as-yet-unmeasurable ETA (`estimated_remaining_s is None`).
    """
    eta = f"{event.estimated_remaining_s:.0f}s" if event.estimated_remaining_s is not None else "?"
    print(
        f"progress: iter={event.iteration} best_objective={event.best_objective:.1f} "
        f"elapsed={event.elapsed_s:.1f}s eta={eta} stagnation={event.stagnation_counter}"
    )


def _render_parallel_progress(event: ParallelProgress) -> None:
    """Aggregated live progress for the parallel solve (Story 14.4, §Cat 8).

    Emitted from the parent on the `--progress-interval` cadence, folding the latest
    per-worker snapshots into one line. `best_worker_objective` is the *leading
    worker's* running top-N sum, not the merged result — it understates the final
    answer (which combines all workers); the run summary's `total_objective` is the
    real, comparable figure. Labelled as such so it can't be misread as the merged
    objective. Uses the stable `progress:` sentinel like the single-process renderer;
    a pure display side-effect (`--quiet` suppresses it).
    """
    print(
        f"progress: workers={event.workers_reporting}/{event.workers_total} "
        f"iters={event.total_iterations} best_worker_objective={event.best_worker_objective:.1f} "
        f"elapsed={event.elapsed_s:.1f}s"
    )


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


def _degradation_message(validated: ValidatedRouteSet, params: SolverParams) -> str | None:
    """Graceful-degradation explanation (FR12), or `None` for a full N-route result.

    When the solver returned fewer than N routes, the area can't yield N under the
    current constraints — so we say so rather than silently loosening them
    (Architecture §"What's not an exception"). Two distinct causes reach this path:
    too few routes clear the route-level slope floor `--theta` (feasibility-bound),
    or enough routes exist but overlap too much to count as distinct under `--j-max`
    (distinctness-bound). The CLI can't tell which bound the solver hit, so the
    message states the observable shortfall and names both tuning levers rather than
    asserting a single cause. The count is `len(validated.routes)`, the same set the
    exit code reads (§Cat 6c): passed and failed routes count alike. `len == 0`
    (empty area) is just the extreme of the same path — no special-casing.
    """
    returned = len(validated.routes)
    if returned >= params.n:
        return None
    # Name the levers honestly. Build the constraint list and the matching lever
    # list together from one flag read so the two can't drift. `--start-at-junction`
    # (FR31) can shrink the feasible set below N on its own (few road/trail
    # junctions in the area), so surface it when active rather than pointing only
    # at --theta / --j-max.
    constraints = f"theta={params.theta:.2f}, J_max <= {params.j_max:.2f}"
    levers = "relax --theta or --j-max"
    if params.start_at_junction:
        constraints += ", start-at-junction"
        levers += " or drop --start-at-junction"
    # --max-descent-slope (FR32) can shrink the feasible set below N on its own
    # (steep terrain leaves few descendable routes), so surface it when active.
    if params.max_descent_slope is not None:
        constraints += f", max-descent-slope={params.max_descent_slope:.2f}"
        levers += " or raise/drop --max-descent-slope"
    return (
        f"Only {returned} of {params.n} requested routes satisfy the current constraints "
        f"({constraints}); {levers} to admit more."
    )


def _run_summary(
    validated: ValidatedRouteSet,
    params: SolverParams,
    status: ConvergenceStatus,
    workers: int,
    merge_interval: int,
    total_objective: float,
    wall_clock_s: float,
    degradation: str | None,
) -> str:
    """Build the end-of-run summary block (Story 7.5, FR22) for stdout.

    A pure formatter so the block is testable without capturing stdout; the caller
    does the single `print`. Labels are stable (tests regex-match them) and the
    `--- Run summary ---` delimiter lets downstream scripts split stdout. Plain
    ASCII, the same §Cat 8 stdout discipline as the progress and cache-hit lines.
    `routes_returned`/`validation_failures` read the same validated set the exit
    code does (§Cat 6c). The `degradation:` line is included only for a degraded
    set (`routes_returned < N`); its value is the explanation already embedded in
    each report, passed in — never recomputed. `seed=none` marks an unseeded run.
    `workers` is the CLI-layer parallel-restart count (Story 14.4), reported
    alongside the params it orchestrates; it is not a `SolverParams` field, so it is
    passed in separately. `iter_budget` shown is always the user's *total* (workers
    each ran a `total // workers` share — an implementation detail, not reported).
    `total_objective` is the summed objective of the returned top-N — the same
    quantity the progress line tracks — so a parallel run's final merged result can
    be compared like-for-like against a single-process run (the parallel *progress*
    line only shows the leading worker's running sum, which understates the merge).
    """
    returned = len(validated.routes)
    failures = sum(1 for r in validated.routes if not r.validation.passed)
    seed = "none" if params.seed is None else params.seed
    lines = [
        "--- Run summary ---",
        (
            f"parameters: theta={params.theta} j_max={params.j_max} n={params.n} "
            f"seed={seed} iter_budget={params.iter_budget} "
            f"time_budget={params.time_budget} stagnation_iters={params.stagnation_iters} "
            f"workers={workers} merge_interval={merge_interval}"
        ),
        f"routes_returned: {returned}/{params.n}",
        f"total_objective: {total_objective:.1f}",
        f"validation_failures: {failures}",
        f"convergence_status: {status}",
    ]
    if degradation is not None:
        lines.append(f"degradation: {degradation}")
    lines.append(f"wall_clock_total: {wall_clock_s:.2f}s")
    return "\n".join(lines)


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
