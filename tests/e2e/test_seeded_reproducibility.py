"""Seeded reproducibility end-to-end (Story 3.11 AC #4 / FR29, NFR4).

Runs the same `steeproute --seed 42` query twice against the same prepared cache
and asserts the JSON sidecars are byte-identical across runs. This verifies the
seed threads cleanly from the CLI through `np.random.default_rng` into GRASP's
construction loop (Story 3.6 already pins byte-identical edge-sets at the solver
layer; this closes the loop end-to-end through render).
"""

from __future__ import annotations

import pathlib
from collections.abc import Callable

from click.testing import Result


def test_same_seed_produces_byte_identical_json(
    seeded_cache: pathlib.Path,
    run_query: Callable[..., Result],
    tmp_path: pathlib.Path,
) -> None:
    out_a = tmp_path / "run-a"
    out_b = tmp_path / "run-b"

    result_a = run_query(seeded_cache, out_a, seed=42)
    result_b = run_query(seeded_cache, out_b, seed=42)

    assert result_a.exit_code == 0, result_a.output
    assert result_b.exit_code == 0, result_b.output

    json_a = sorted(out_a.glob("route-*.json"))
    json_b = sorted(out_b.glob("route-*.json"))
    assert json_a, "expected at least one JSON sidecar"
    assert [p.name for p in json_a] == [p.name for p in json_b]

    for path_a, path_b in zip(json_a, json_b, strict=True):
        assert path_a.read_bytes() == path_b.read_bytes(), (
            f"{path_a.name} differs between two --seed 42 runs (FR29/NFR4 broken)"
        )
