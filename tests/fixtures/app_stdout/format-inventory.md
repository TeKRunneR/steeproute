# CLI stdout format inventory (App progress-classifier spec)

Pinned output of the steeproute CLIs' **stdout**, captured from real
subprocesses, for the web App's stdout→progress classifier
(`steeproute.app.cli_adapter.progress_parse`, architecture-app.md §Category 3).
These `*.stdout.txt` files are the unit-test inputs for
`tests/unit/test_app_progress_parse.py` (App stories 1.4 / 2.2). Every
distinguishable line type below is described precisely enough to write one
classifier rule and mapped to its target field in the unified `ProgressModel`:

```
{phase, stage_name, stage_index, stage_total, grasp:{iter, best_cost}|null, elapsed, log_tail}
```

`grasp` is populated only during a query's solve phase; `null` (present, never
omitted) otherwise, and always `null` for `setup` jobs.

---

## Capture provenance (what produced these fixtures)

- **Area:** center `45.260,5.788` (Le Sappey-en-Chartreuse, Chartreuse massif —
  the same trail-rich spot as `tests/fixtures/grenoble_small`). Setup radius `2`
  km (`--force-refresh`, forced cache-miss); query radius `1.5` km (strict
  containment for the FR24 coverage check).
- **Query params = the quality demo params** (architecture-app.md §Category 9;
  AGENTS.md): `--iter-budget 200000 --stagnation-iters 10000 --difficulty-cap T4
  --elevation-deadband 1 --seed 42`.
- **Real `uv run` subprocesses** (steeproute `0.0.1.dev117+585262f`), stdout
  captured verbatim; stderr was empty for all three runs. This is the exact
  surface the App's subprocess reader classifies.

### Deviations / redactions (AC #4)

- **`--progress-interval 0.05`** was passed to the two query captures (CLI
  default is `5.0` s). Reason: this small area's solve converged in ~1–2 s, so
  the default 5 s throttle emitted **zero** `progress:` lines (a first run at
  the plain demo params produced none). `--progress-interval` is a pure
  display-cadence knob — it never touches the solver RNG or results
  (byte-identical edge-sets, FR29) — so the captured line **shape** is identical
  to a production run; only the number/spacing of `progress:` lines differs. A
  real query on a larger area shows these same shapes at the 5 s cadence.
- **Setup `entry:` path redacted:** the machine/session-specific absolute prefix
  was replaced with `<CACHE_ROOT>`; the trailing `steeproute\areas\<hash>`
  structure and the 16-hex `cache_key_hash` are the real captured values.
- **`eta=?` not observed:** every single-process `progress:` line here had a
  measurable ETA (`eta=<int>s`). The `eta=?` variant (unmeasurable) is real —
  documented below from source — it just didn't occur in this fast run.

---

## The three flavours

| Fixture | Job kind | Flavours present |
|---|---|---|
| `setup_cache_miss.stdout.txt` | `setup` | A (setup stages) |
| `query_workers1.stdout.txt` | `query` | B (query stages) + C single-process GRASP |
| `query_workers4.stdout.txt` | `query` | B (query stages) + C **parallel** GRASP |

### ⚠️ Key finding: no `n/total` on the wire

The CLI stage lines carry **only a stage name — never an `n/total` count.** So
`stage_index` / `stage_total` are **not parsed from stdout**; the classifier
must derive them from a **known, ordered stage list per job kind** and increment
as each `stage: … ...` start line arrives:

- **setup (cache-miss), 7 stages, in order:** `osm-download`, `trail-filter`,
  `polyline-smoothing`, `resampling`, `dem-resolve`, `elevation-sampling`,
  `cache-write`.
- **query, 6 stages, in order:** `load-prepared-area`, `elevation-reshape`,
  `trail-filter`, `climb-detection`, `climb-contraction`, `validate-render`.

`trail-filter` appears in **both** kinds, so a name→index lookup is ambiguous —
track position **positionally within the run**, not by name. A **setup
cache-hit** emits **zero** stage lines (only the summary block), so a cache-hit
`setup` job has no stage sequence at all — the classifier must tolerate that.

---

## Flavour A — setup stage lines

Emitted by the `StageProgress` seam (`src/steeproute/progress.py`), sink is
`print`; suppressed entirely by `--quiet`.

**A1 — stage start**
```
stage: osm-download (one Overpass request; typically takes minutes) ...
stage: trail-filter ...
```
- Shape: `stage: <name>[ (<note>)] ...`  (the ` (<note>)` is optional).
- `<note>` is a human honesty annotation on the **start line only** — strip it.
  Canonical `stage_name` = text between `stage: ` and the first ` (` or ` ...`.
- → enter phase `setup`; set `stage_name`; advance `stage_index`.

**A2 — within-stage line** (indented 2 spaces)
```
  tile 0/1
  tile 1/1
```
- Only current instance: the DEM fetch loop's `  tile <done>/<total>`
  (`src/steeproute/pipeline/dem_download.py`). Counter **starts at 0** then
  increments. Not a stage boundary.
- → append to `log_tail` (optionally surface as sub-progress of the current
  stage). Does **not** change `stage_index`.

**A3 — stage done**
```
stage: osm-download: 7.69 s
```
- Shape: `stage: <name>: <elapsed> s`, `<elapsed>` is `%.2f`. Name here is the
  **clean** name (never carries the note).
- → record `elapsed` for the stage; stage complete.

**A4 — summary block** (always printed, even under `--quiet`;
`src/steeproute/cli/setup.py`)
```
steeproute-setup: cache-miss
  cache_key_hash: fb7092ddd1059ea2
  entry: <CACHE_ROOT>\steeproute\areas\fb7092ddd1059ea2
  elapsed: 12.54 s
```
- Line 1: `steeproute-setup: <cache-miss|cache-hit>`. On **cache-hit** this
  block is the *only* setup output (no A1–A3).
- → terminal marker: the `setup` job reached `done` (SSE `status` event).

Setup emits **no** `progress:` line ⇒ `grasp` stays `null` for setup jobs.

---

## Flavour B — query non-solve stage lines

Same `StageProgress` seam, reused query-side (`src/steeproute/cli/query.py`).
Same A1/A3 shapes as setup. Observed notes: `elevation-reshape (stages 6-7)`,
`trail-filter (difficulty-cap redux)`.

**B-cue — cache-hit line** (single line, distinct from setup's multi-line block)
```
steeproute: cache-hit cache_key_hash: fb7092ddd1059ea2
```
- Shape: `steeproute: cache-hit cache_key_hash: <16-hex>`. Appears right after
  the `load-prepared-area` done line. → informational; `phase` stays in setup
  stages until the solve.

**B-summary — end-of-run block** (always printed;
`src/steeproute/cli/query.py::_run_summary`)
```
--- Run summary ---
parameters: theta=0.2 j_max=0.3 n=5 seed=42 iter_budget=200000 time_budget=600.0 stagnation_iters=10000 workers=1 merge_interval=250000
routes_returned: 5/5
total_objective: 9719.6
validation_failures: 0
convergence_status: converged
wall_clock_total: 2.00s
```
- `--- Run summary ---` is a stable delimiter.
- `parameters:` — space-separated `key=value` tokens; `seed=none` when unseeded.
- `routes_returned: <k>/<N>`, `total_objective: %.1f`, `validation_failures:
  <k>`.
- `convergence_status:` value set = **`converged` | `budget-exhausted` |
  `interrupted`** (`src/steeproute/models.py::ConvergenceStatus`).
- **optional** `degradation: <msg>` line — present **only** when
  `routes_returned < N` (not in these fixtures; both returned 5/5).
- `wall_clock_total: %.2fs`.
- → terminal marker: query job `done`. `total_objective` is the comparable
  final objective (see the parallel caveat below).

---

## Flavour C — GRASP solver events

Throttled `progress:` lines (`src/steeproute/cli/query.py::_render_progress` /
`_render_parallel_progress`). They appear **between** `stage: climb-contraction:
… s` and `stage: validate-render ...` — i.e. the solve phase. Every line starts
with the stable sentinel `progress: ` (deliberately chosen so it can never be
confused with `stage:` or the summary).

**C1 — single-process** (`--workers 1`, the default)
```
progress: iter=1 best_objective=277.1 elapsed=0.0s eta=30s stagnation=0
progress: iter=24510 best_objective=9719.6 elapsed=1.6s eta=11s stagnation=9631
```
- Shape: `progress: iter=<int> best_objective=<%.1f> elapsed=<%.1f>s
  eta=<eta> stagnation=<int>`.
- `eta` ∈ { `<int>s` (measurable, `%.0f`) | `?` (unmeasurable — when
  `estimated_remaining_s is None`, i.e. iteration ≤ 0 or elapsed ≤ 0; see
  `src/steeproute/progress.py::estimate_remaining`) }.
- `best_objective` is **not monotonic** (the top-N overlap-eviction branch can
  step it down — see `ProgressEvent` docstring).
- → phase `solve`; `grasp.iter = iter`; `grasp.best_cost = best_objective`;
  `elapsed = elapsed`. (`stagnation`/`eta` have no `ProgressModel` field — drop
  or push to `log_tail`.)

**C2 — parallel** (`--workers > 1`)
```
progress: workers=1/4 iters=568 best_worker_objective=5387.5 elapsed=0.9s
progress: workers=4/4 iters=158393 best_worker_objective=10118.8 elapsed=9.1s
```
- Shape: `progress: workers=<reporting>/<total> iters=<int>
  best_worker_objective=<%.1f> elapsed=<%.1f>s`.
- **Disambiguate C1 vs C2 by the first token after `progress: `**: `iter=` →
  single-process, `workers=` → parallel.
- ⚠️ `best_worker_objective` is the **leading worker's running sum, not the
  merged result** — it *understates* the final answer. In `query_workers4`
  the running max reaches ~10118 while the summary's `total_objective` is
  **10670.1**. Map it to `grasp.best_cost` if you must, but the honest final
  figure is `B-summary.total_objective`.
- → phase `solve`; `grasp.iter = iters` (aggregate); `grasp.best_cost =
  best_worker_objective` (with the caveat above).

---

## Stream discipline (what the classifier reads)

- **stdout** carries everything above: `stage:` lines, `  tile …`, the
  `steeproute[-setup]:` cue/summary, `--- Run summary ---` block, and
  `progress:` lines. **This is the only stream the App subprocess reader
  classifies.**
- **stderr** carries `logging` output (OSM-age warnings, `--verbose` DEBUG), the
  parallel→single-process fallback `warning: …`, and `interrupted before any
  solution found`. **Not** a classifier input (architecture-app.md §Process
  patterns: scraped CLI stdout is data; server logging is separate).

---

## ProgressModel field map (quick reference)

| stdout line | `phase` | `stage_name` | `stage_index/total` | `grasp` | `elapsed` |
|---|---|---|---|---|---|
| A1 `stage: X ...` | setup | X | ++ / known count | null | — |
| A2 `  tile i/N` | setup | (unchanged) | (unchanged) | null | — → `log_tail` |
| A3 `stage: X: t s` | setup | X | (complete) | null | t |
| A4 `steeproute-setup: …` | setup | — | — | null | (summary) |
| B1 `stage: X ...` | query | X | ++ / 6 | null | — |
| B3 `stage: X: t s` | query | X | (complete) | null | t |
| B-cue cache-hit | query | (unchanged) | — | null | — |
| C1 `progress: iter=…` | solve | (unchanged) | (unchanged) | {iter, best_cost} | elapsed |
| C2 `progress: workers=…` | solve | (unchanged) | (unchanged) | {iter=iters, best_cost=best_worker_objective*} | elapsed |
| B-summary block | (terminal) | — | — | — | wall_clock_total |

\* understates the merged result; prefer `total_objective` from the summary.
