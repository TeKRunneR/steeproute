"""Pinned-regression golden harness + `update-regression` entry point (Architecture §Cat 11c/11d).

A *golden* captures one fixture's current known-good GRASP output: a 5-field hash
tuple per route — `objective`, `d_plus_m`, `d_minus_m`, `edge_count`,
`canonical_edge_sequence_hash` — plus the `seed` and a `params_hash` over the
explicitly-pinned param set. `tests/e2e/test_pinned_regressions.py` and
`uv run update-regression` both go through this module, so the comparison and the
writer can never disagree on what a golden *should* contain (Architecture §Cat 11d).

Two deliberate design choices (Story 8.1):

- **`objective` is derived, not read.** The JSON sidecar (`output.py`) carries
  `metrics` + `edges` but no `objective`; the solver defines
  `Solution.objective = Σ(d_plus_m + d_minus_m)` over a route's edges
  (`solver/grasp.py`), so we recompute it as `d_plus_m + d_minus_m`. If a future
  objective ever diverges from D+ + D−, the sidecar must start carrying it.
- **`params_hash` covers only the *pinned* set**, not the whole `SolverParams`
  dataclass. A fixture pins every behavior-affecting knob explicitly, so a default
  re-tuning can't silently move a golden — yet adding a brand-new `SolverParams`
  field (that the fixture doesn't pin) leaves `params_hash` and the route output
  untouched, so existing goldens stay green with zero edits. New behavior that
  *changes* output gets its own fixture + golden, never a rewrite of an existing one.

This is a repo-local dev tool: `FIXTURES` and the golden/cache paths resolve against
the source tree, so `update-regression` is meant to be run via `uv run` from the repo,
not from an installed wheel. Story 8.1 ships one proof fixture (`grenoble_small`);
Story 8.2 adds the 2–3 Grenoble cutouts and the zero-tolerance CI gate.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from steeproute.cache import sha256_canonical, write_json_atomic

# JSON-loaded payloads (route sidecars, goldens) are `Any`-valued at this data
# boundary — the project's external-boundary convention (Architecture §"Type hints
# and data"; `reportAny` is off in pyproject).
Sidecar = dict[str, Any]
Golden = dict[str, Any]

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FIXTURES_ROOT = _REPO_ROOT / "tests" / "e2e" / "fixtures"
GOLDENS_DIR = _REPO_ROOT / "tests" / "e2e" / "goldens"

# The 5 per-route fields a golden pins (Architecture §Cat 11d). `canonical_edge_
# sequence_hash` is what catches a silently-changed route that happens to collide
# on the four scalar metrics.
_ROUTE_FIELDS: tuple[str, ...] = (
    "objective",
    "d_plus_m",
    "d_minus_m",
    "edge_count",
    "canonical_edge_sequence_hash",
)


@dataclass(frozen=True)
class Fixture:
    """One pinned regression fixture: a queryable cache + the exact knobs to run it at.

    `cache_dir` is a full cache root (`steeproute/index.json` + `areas/<hash>/...`)
    the `steeproute` query CLI can run against with a plain `--cache-dir`, no patching.
    `pinned_params` maps every behavior-affecting CLI flag to its value as a string —
    pinned explicitly (never inherited from CLI defaults) so a default re-tuning or a
    new param can't silently move the golden. It must keep the run deterministic
    (FR29): `--time-budget` is pinned high so termination is purely iteration-based
    (iter-budget / stagnation), never wall-clock.

    `min_routes` is a sanity floor (default 1): `run_fixture` refuses to return a run
    that produced fewer routes. The committed golden already pins the *exact* route
    set, so this guards the one case the golden can't — a run that silently collapses
    to (near-)zero routes being baked into a fresh golden by `update-regression`,
    turning a real regression into a green no-op gate.
    """

    name: str
    cache_dir: pathlib.Path
    center: tuple[float, float]
    radius_km: float
    seed: int
    pinned_params: dict[str, str] = field(default_factory=dict)
    min_routes: int = 1
    # Regression tier. `"fast"` (the default) runs at low budgets as a cheap
    # determinism smoke; `"realistic"` mirrors the budgets the tool is actually
    # used at (`REALISTIC_FIXTURES`), gated `slow` in the suite. The tier
    # disambiguates the golden path so the two regimes' goldens never collide.
    tier: str = "fast"


# CLI flags pinned as booleans (`click` on/off switches). In `pinned_params` their
# value is the string `"true"`/`"false"`; `run_fixture` renders `"true"` as the bare
# flag and `"false"` as nothing. Membership here is keyed by flag *name*, not by the
# value string, so a value-taking param whose value happens to be `"true"` still
# renders as a `--flag value` pair (Story 10.1 review #5).
_BOOLEAN_FLAGS: frozenset[str] = frozenset({"--start-at-junction"})


# The pinned param set shared by every fixture. Pinned explicitly (never inherited
# from CLI defaults) so a default re-tuning or a new param can't silently move a
# golden. `--time-budget` is pinned high so the wall-clock terminator never binds —
# termination is iteration-based (stagnation/iter-budget) and therefore deterministic
# (FR29).
_PINNED_PARAMS: dict[str, str] = {
    "--theta": "0.20",
    "--min-climb-slope": "0.20",
    "--difficulty-cap": "T3",
    "--l-connector": "200.0",
    "--min-climb-ground-length": "300.0",
    "--elevation-smoothing": "50.0",
    "--elevation-deadband": "0.0",
    "--j-max": "0.30",
    "--n": "5",
    "--untagged-trails": "include",
    "--iter-budget": "2000",
    "--stagnation-iters": "100",
    "--time-budget": "100000",
}

# Realistic-tier budgets: the regime the tool is actually used at (~200k iters /
# ~10k stagnation window), where GRASP converges to quality routes rather than the
# unconverged low-budget output the fast tier pins. Everything else matches
# `_PINNED_PARAMS`; only the two termination budgets move. Still deterministic
# (FR29) — `--time-budget` stays high so wall-clock never binds.
_REALISTIC_PARAMS: dict[str, str] = {
    **_PINNED_PARAMS,
    "--iter-budget": "200000",
    "--stagnation-iters": "10000",
}


FIXTURES: tuple[Fixture, ...] = (
    Fixture(
        name="grenoble_small",
        cache_dir=_FIXTURES_ROOT / "grenoble_small" / "cache",
        center=(45.260, 5.788),
        radius_km=1.5,
        seed=42,
        pinned_params=dict(_PINNED_PARAMS),
    ),
    # The three Story 8.2 cutouts: distinct Grenoble-massif terrain (Belledonne,
    # Vercors, Chartreuse). Each cache was prepared by real `steeproute-setup` at a
    # 2.0 km seed radius; the regression query runs at 1.5 km so it is strictly
    # contained in the prepared bbox (FR24 — `check_coverage` uses strict containment).
    Fixture(
        name="belledonne",
        cache_dir=_FIXTURES_ROOT / "belledonne" / "cache",
        center=(45.186753, 5.961482),
        radius_km=1.5,
        seed=42,
        pinned_params=dict(_PINNED_PARAMS),
    ),
    Fixture(
        name="vercors",
        cache_dir=_FIXTURES_ROOT / "vercors" / "cache",
        center=(45.148755, 5.639232),
        radius_km=1.5,
        seed=42,
        pinned_params=dict(_PINNED_PARAMS),
    ),
    Fixture(
        name="chartreuse",
        cache_dir=_FIXTURES_ROOT / "chartreuse" / "cache",
        center=(45.374716, 5.772793),
        radius_km=1.5,
        seed=42,
        pinned_params=dict(_PINNED_PARAMS),
    ),
)


# The realistic-budget tier: the same caches/centers/seeds as the fast tier, run at
# `_REALISTIC_PARAMS` so the goldens pin the converged regime the tool is used in.
# Gated `slow` (`tests/e2e/test_pinned_regressions.py`); regenerate with
# `uv run update-regression --all --tier realistic`.
REALISTIC_FIXTURES: tuple[Fixture, ...] = tuple(
    replace(f, pinned_params=dict(_REALISTIC_PARAMS), tier="realistic") for f in FIXTURES
)


# Flag-on goldens (Epic 10): each pins one new opt-in constraint *on*, on a real
# cache, leaving the default-param goldens above untouched (the non-regression
# proof). Deliberately kept OUT of `FIXTURES` for now — folding these into the
# zero-tolerance CI gate + the realistic tier is Story 8.5's job; Story 10.1 only
# creates the fixture, its committed golden, and the junction-start property
# assertion (`tests/e2e/test_junction_start.py`). `--start-at-junction` is a
# boolean pinned param (`"true"`/`"false"`); `run_fixture` renders it as a bare
# flag rather than a `--flag value` pair.
FLAG_ON_FIXTURES: tuple[Fixture, ...] = (
    Fixture(
        name="grenoble_small_junction",
        cache_dir=_FIXTURES_ROOT / "grenoble_small" / "cache",
        center=(45.260, 5.788),
        radius_km=1.5,
        seed=42,
        pinned_params={**_PINNED_PARAMS, "--start-at-junction": "true"},
    ),
)


def canonical_edge_sequence_hash(edges: Iterable[Sequence[int]]) -> str:
    """SHA256 over a route's edges sorted by `(node_u, node_v, key)` (Architecture §Cat 11d).

    Sorting by the canonical edge-identity tuple (Implementation Patterns §"Numerical
    and data discipline") makes the hash capture graph-level edge identity independent
    of traversal-order serialization — `(objective, D+, D−, edge_count)` can collide
    while the underlying route silently changes, so this field is what notices.
    """
    canonical = sorted([int(e[0]), int(e[1]), int(e[2])] for e in edges)
    return sha256_canonical(canonical)


def params_hash(pinned_params: dict[str, str]) -> str:
    """SHA256 over the fixture's explicitly-pinned param set (see module docstring)."""
    return sha256_canonical(pinned_params)


def route_tuple(sidecar: Sidecar) -> Golden:
    """The 5-field golden tuple for one route, derived from its JSON sidecar.

    `objective` is recomputed as `d_plus_m + d_minus_m` (the solver's objective);
    `edge_count` and the canonical hash come from the sidecar's `edges` list.
    """
    metrics = sidecar["metrics"]
    edges = sidecar["edges"]
    d_plus_m = metrics["d_plus_m"]
    d_minus_m = metrics["d_minus_m"]
    return {
        "route_index": sidecar["route_index"],
        "objective": d_plus_m + d_minus_m,
        "d_plus_m": d_plus_m,
        "d_minus_m": d_minus_m,
        "edge_count": len(edges),
        "canonical_edge_sequence_hash": canonical_edge_sequence_hash(edges),
    }


def build_golden(fixture: Fixture, sidecars: Iterable[Sidecar]) -> Golden:
    """Assemble the golden dict for `fixture` from its route sidecars (ordered by route_index)."""
    routes = [route_tuple(s) for s in sorted(sidecars, key=lambda s: s["route_index"])]
    return {
        "fixture_name": fixture.name,
        "seed": fixture.seed,
        "params_hash": params_hash(fixture.pinned_params),
        "routes": routes,
    }


def golden_path(fixture: Fixture) -> pathlib.Path:
    """`tests/e2e/goldens/<fixture_name>[.<tier>].json` (the fast tier carries no suffix).

    The tier suffix keeps the fast and realistic goldens for one fixture from
    colliding; fast stays un-suffixed so existing Story 8.1/8.2 goldens keep their
    committed paths.
    """
    suffix = "" if fixture.tier == "fast" else f".{fixture.tier}"
    return GOLDENS_DIR / f"{fixture.name}{suffix}.json"


def read_golden(fixture: Fixture) -> Golden | None:
    """Load the committed golden for `fixture`, or `None` if it doesn't exist yet."""
    path = golden_path(fixture)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_golden(fixture: Fixture, golden: Golden) -> None:
    """Atomically write `fixture`'s golden to disk (creates `tests/e2e/goldens/` if needed)."""
    GOLDENS_DIR.mkdir(parents=True, exist_ok=True)
    write_json_atomic(golden_path(fixture), golden)


def run_fixture(fixture: Fixture) -> list[Sidecar]:
    """Run `steeproute` against `fixture`'s cache at its pinned params; return route sidecars.

    Invokes the real query CLI in-process against the committed cache (no patching of
    the solver or output layers — Story 8.1 AC) into a throwaway output dir, then reads
    the `route-*.json` sidecars back. Exit 0 (all routes valid) and exit 1 (some route
    failed validation) are both acceptable — a regression golden pins whatever the run
    deterministically produces; any other exit code is a harness error.
    """
    from click.testing import CliRunner

    from steeproute.cli.query import cli as query_cli

    with tempfile.TemporaryDirectory() as tmp:
        out_dir = pathlib.Path(tmp)
        args = [
            "--center",
            f"{fixture.center[0]},{fixture.center[1]}",
            "--radius",
            str(fixture.radius_km),
            "--cache-dir",
            str(fixture.cache_dir),
            "--output-dir",
            str(out_dir),
            "--seed",
            str(fixture.seed),
            "--quiet",
        ]
        for flag, value in fixture.pinned_params.items():
            # Boolean pinned params (`_BOOLEAN_FLAGS`, e.g. `--start-at-junction`)
            # render as a bare flag, not a `--flag value` pair: `"true"` emits the
            # flag, `"false"` emits nothing. Pinning the value in the dict (rather
            # than appending the bare flag) keeps it inside the `params_hash`
            # fingerprint so a flag-on golden can't be confused with its flag-off
            # sibling. Keyed by flag name so a value-taking param whose value is
            # the literal `"true"` is unaffected; an unexpected value fails loud.
            if flag in _BOOLEAN_FLAGS:
                if value not in ("true", "false"):
                    raise ValueError(
                        f"boolean pinned param {flag!r} must be 'true' or 'false', got {value!r}"
                    )
                if value == "true":
                    args.append(flag)
            else:
                args += [flag, value]
        result = CliRunner().invoke(query_cli, args, catch_exceptions=False)
        if result.exit_code not in (0, 1):
            raise RuntimeError(
                f"query failed for fixture {fixture.name!r} (exit {result.exit_code}):\n"
                f"{result.output}"
            )
        sidecars = [
            json.loads(path.read_text(encoding="utf-8")) for path in out_dir.glob("route-*.json")
        ]
        if len(sidecars) < fixture.min_routes:
            raise RuntimeError(
                f"fixture {fixture.name!r} produced {len(sidecars)} route(s), expected at "
                f"least {fixture.min_routes} (min_routes floor). A run that collapses to "
                f"(near-)zero routes must not be captured as a golden — investigate the "
                f"regression rather than re-baselining."
            )
        return sidecars


def diff_goldens(old: Golden | None, new: Golden) -> str:
    """Human-readable before/after diff of two goldens, for `update-regression` output."""
    if old is None:
        return f"  (no previous golden - {len(new['routes'])} routes captured fresh)"  # type: ignore[arg-type]

    lines: list[str] = []
    for key in ("seed", "params_hash"):
        if old.get(key) != new.get(key):
            lines.append(f"  {key}: {old.get(key)} -> {new.get(key)}")

    old_routes = {r["route_index"]: r for r in old["routes"]}  # type: ignore[union-attr]
    new_routes = {r["route_index"]: r for r in new["routes"]}  # type: ignore[union-attr]
    for idx in sorted(old_routes.keys() | new_routes.keys()):
        old_r = old_routes.get(idx)
        new_r = new_routes.get(idx)
        if old_r is None:
            lines.append(f"  route {idx}: ADDED")
            continue
        if new_r is None:
            lines.append(f"  route {idx}: REMOVED")
            continue
        for fld in _ROUTE_FIELDS:
            if old_r.get(fld) != new_r.get(fld):
                lines.append(f"  route {idx}.{fld}: {old_r.get(fld)} -> {new_r.get(fld)}")

    return "\n".join(lines) if lines else "  (no change)"


def _select(fixture_name: str | None, all_fixtures: bool, tier: str) -> list[Fixture]:
    pool = REALISTIC_FIXTURES if tier == "realistic" else FIXTURES
    if all_fixtures:
        # `--all` regenerates the standard tier only — the Epic 10 `FLAG_ON_FIXTURES`
        # are not yet part of the bulk/CI set (folded in by Story 8.5). They remain
        # individually regenerable by name below.
        return list(pool)
    # Named lookup also searches the fast-tier flag-on fixtures, so
    # `update-regression --fixture grenoble_small_junction` works.
    named_pool = list(pool) + (list(FLAG_ON_FIXTURES) if tier == "fast" else [])
    selected = [f for f in named_pool if f.name == fixture_name]
    if not selected:
        known = ", ".join(f.name for f in named_pool) or "(none registered)"
        raise SystemExit(
            f"Unknown fixture {fixture_name!r} in tier {tier!r}. Known fixtures: {known}."
        )
    return selected


def main() -> None:
    """`update-regression` entry point: re-run fixture(s), print a diff, overwrite golden(s)."""
    parser = argparse.ArgumentParser(
        prog="update-regression",
        description="Regenerate pinned-regression goldens. Commits updating a golden "
        "MUST state an explicit rationale in the commit message.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--fixture", metavar="NAME", help="regenerate one fixture's golden")
    group.add_argument("--all", action="store_true", help="regenerate every fixture's golden")
    parser.add_argument(
        "--tier",
        choices=("fast", "realistic"),
        default="fast",
        help="which regression tier to regenerate (default: fast)",
    )
    ns = parser.parse_args()

    for fixture in _select(ns.fixture, ns.all, ns.tier):
        old = read_golden(fixture)
        new = build_golden(fixture, run_fixture(fixture))
        print(f"== {fixture.name} ==")
        print(diff_goldens(old, new))
        write_golden(fixture, new)
        print(f"  wrote {golden_path(fixture)}")

    print("\nRemember: commit golden changes with an explicit rationale in the message.")


if __name__ == "__main__":
    main()
