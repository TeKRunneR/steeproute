"""Unit tests for the canonical edge-sequence hash (Story 8.1 AC #6 / Architecture §Cat 11d).

The hash is the regression harness's mutation detector: it must be (a) stable across
runs so a committed golden stays comparable, and (b) sensitive to any change in the
route's edge identity so a silently-altered route can't slip past on matching scalars.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from steeproute.regression import (
    FIXTURES,
    FLAG_ON_FIXTURES,
    REALISTIC_FIXTURES,
    Fixture,
    area_args,
    canonical_edge_sequence_hash,
    golden_path,
    params_hash,
    read_golden,
)

# A route's edges as (node_u, node_v, key) triples.
_EDGES = [(1, 2, 0), (2, 3, 0), (3, 1, 1)]

# Every registered fixture across all three tiers (fast / realistic / flag-on).
# `name` alone is not unique — a fast and a realistic fixture share it — so tests
# that parametrize over this must include `tier` in their ids.
_ALL_FIXTURES: tuple[Fixture, ...] = (*FIXTURES, *REALISTIC_FIXTURES, *FLAG_ON_FIXTURES)


def test_hash_is_stable_across_runs() -> None:
    """SHA256 over the canonical serialization is process-independent (FR29).

    Pinned to a known digest so a change to the serialization scheme (sort rule,
    separators) is caught here rather than silently invalidating every golden.
    """
    assert (
        canonical_edge_sequence_hash(_EDGES)
        == "761bd353af9799d9a0ba31e562f9415b0f94e32fb52756949a0b4c38bdbdd421"
    )


def test_hash_is_repeatable() -> None:
    assert canonical_edge_sequence_hash(_EDGES) == canonical_edge_sequence_hash(list(_EDGES))


def test_hash_is_canonical_over_traversal_order() -> None:
    """Same edge set, different serialization order -> same hash (edges are sorted first)."""
    shuffled = [(3, 1, 1), (1, 2, 0), (2, 3, 0)]
    assert canonical_edge_sequence_hash(_EDGES) == canonical_edge_sequence_hash(shuffled)


def test_hash_changes_on_single_edge_substitution() -> None:
    """Swapping one edge for another changes the digest (mutation detection)."""
    mutated = [(1, 2, 0), (2, 3, 0), (3, 4, 1)]  # last edge's node_v 1 -> 4
    assert canonical_edge_sequence_hash(_EDGES) != canonical_edge_sequence_hash(mutated)


def test_hash_distinguishes_parallel_edges_by_key() -> None:
    """Parallel edges between the same node pair differ only by `key` and must not collide."""
    assert canonical_edge_sequence_hash([(1, 2, 0)]) != canonical_edge_sequence_hash([(1, 2, 1)])


def test_hash_is_direction_sensitive() -> None:
    """A directed edge and its reverse are distinct identities."""
    assert canonical_edge_sequence_hash([(1, 2, 0)]) != canonical_edge_sequence_hash([(2, 1, 0)])


# --- Fixture area argv (Story 15.3) ---------------------------------------


def test_square_fixtures_emit_the_pre_rotation_argv() -> None:
    """The harness learning a second area spelling must not move an existing golden.

    Every square fixture's area fragment has to stay exactly `--radius <value>` —
    the argv Stories 8.1/8.2 baked their goldens with.
    """
    for fixture in FIXTURES:
        if fixture.width_km is None:
            assert area_args(fixture) == ["--radius", str(fixture.radius_km)]


def test_rotated_fixture_emits_full_dimensions_and_bearing() -> None:
    """A rotated fixture spells the CLI's own flags, with `--width`/`--height` full-size."""
    rotated = next(f for f in FIXTURES if f.name == "grenoble_small_rotated")
    assert area_args(rotated) == ["--width", "3.0", "--height", "1.6", "--angle", "45.0"]


def test_axis_aligned_rectangle_omits_the_bearing() -> None:
    """`--angle 0` is the click default, so an unrotated rectangle leaves it off."""
    rect = replace(FIXTURES[0], width_km=4.0, height_km=2.0, angle_deg=0.0)
    assert area_args(rect) == ["--width", "4.0", "--height", "2.0"]


def test_half_specified_rectangle_fails_loud() -> None:
    """One dimension of two is a harness authoring error, not a silent square."""
    broken = replace(FIXTURES[0], width_km=4.0, height_km=None)
    with pytest.raises(ValueError, match="only one of width_km/height_km"):
        area_args(broken)


def test_every_fixture_has_a_distinct_golden_path() -> None:
    """Two fixtures sharing a golden file would silently overwrite each other."""
    paths = [golden_path(f) for f in _ALL_FIXTURES]
    assert len(set(paths)) == len(paths)


# --- Committed goldens agree with their fixture definitions ----------------


@pytest.mark.parametrize("fixture", _ALL_FIXTURES, ids=lambda f: f"{f.name}-{f.tier}")
def test_committed_golden_metadata_matches_its_fixture(fixture: Fixture) -> None:
    """A committed golden's non-route metadata must still describe its fixture.

    Pure metadata: reads the committed JSON and re-hashes `pinned_params`, with no
    solver run — so unlike `test_pinned_regressions.py` this is cheap enough to stay
    in the default suite for *every* tier, including the `slow`-gated realistic one.

    That gap is why this test exists. On 2026-07-28
    (`spec-cli-defaults-and-setup-radius-cap.md`) `"--workers": "1"` was added to
    `_PINNED_PARAMS`, which `_REALISTIC_PARAMS` spreads — so `params_hash` moved for
    both tiers, but only the fast + flag-on goldens were rebaked. The five stale
    `*.realistic.json` goldens then failed on `params_hash` alone, and nothing
    noticed until someone ran `-m slow` by hand, because the realistic tier is
    deselected by `addopts`. Re-deriving the hash costs microseconds, so the whole
    class of "a pinned param moved and some tier's goldens weren't regenerated"
    fails here immediately instead of lying dormant in a tier nobody selects.
    """
    golden = read_golden(fixture)
    assert golden is not None, (
        f"no committed golden at {golden_path(fixture)} for {fixture.name!r} ({fixture.tier} tier)"
    )

    assert golden["params_hash"] == params_hash(fixture.pinned_params), (
        f"{golden_path(fixture).name} is stale: its params_hash predates the current "
        f"pinned param set. If a pinned param changed deliberately, rebake with "
        f"`uv run update-regression --fixture {fixture.name}"
        f"{'' if fixture.tier == 'fast' else f' --tier {fixture.tier}'}` and commit "
        f"with a rationale - checking the route tuples are unchanged if only the "
        f"hash's input set moved."
    )
    # Cheap identity checks alongside: a golden copied to the wrong path, or a
    # fixture's seed edited without a rebake, are the same class of staleness.
    assert golden["fixture_name"] == fixture.name
    assert golden["seed"] == fixture.seed
