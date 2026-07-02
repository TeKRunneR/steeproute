---
stepsCompleted: [1, 2, 3, 4, 5, 6]
inputDocuments: ['_bmad-output/planning-artifacts/future-ideas.md']
workflowType: 'research'
lastStep: 6
research_type: 'technical'
research_topic: 'steeproute solver performance tuning: profiling methodology and speed-up options (vectorization, native extensions, Rust)'
research_goals: 'End-to-end decision support: how to profile the Python GRASP solver to find real bottlenecks, then which speed-up path to take (NumPy vectorization, Cython, Rust via PyO3, full Rust rewrite). Rust is evaluated on both wall-clock gain and learning value.'
user_name: 'Yann'
date: '2026-07-02'
web_research_enabled: true
source_verification: true
---

# Research Report: technical

**Date:** 2026-07-02
**Author:** Yann
**Research Type:** technical

---

## Research Overview

This report covers performance tuning for steeproute end-to-end: how to profile both workloads (the CPU-bound GRASP solver and the I/O-heavy `steeproute-setup` pipeline), which speed-up paths are realistic, and how to sequence them. Rust was scoped to hot-loop extraction via PyO3 (Yann starts from ~zero Rust), evaluated on wall-clock gain and learning value; a full rewrite is out of scope unless profiling shows no extractable hot core.

The research proceeded in five web-verified passes — technology stack, integration patterns, architectural patterns, implementation approaches — over current (2025–2026) sources, with confidence levels flagged where evidence is thin or conflicting. The bottom line: instrument and profile before touching anything (Phases 0–2 are small and independently valuable), take the cheap wins profiling indicts, and only then decide between rustworkx (Rust speed without writing Rust) and a PyO3 kernel (the learning-value path, de-risked by an extract-interface-first refactor). Full findings and the phased roadmap are synthesized in the **Research Synthesis** section at the end of this document.

---

## Technical Research Scope Confirmation

**Research Topic:** steeproute solver performance tuning: profiling methodology and speed-up options (vectorization, native extensions, Rust hot-loop extraction)
**Research Goals:** End-to-end decision support: how to profile the full steeproute pipeline (GRASP solver + setup CLI data download/preparation) to find real bottlenecks, then which speed-up path to take. Rust is scoped to hot-loop extraction via PyO3 (user knows ~zero Rust; full rewrite only if research shows cost is diffuse with no extractable core), evaluated on both wall-clock gain and learning value.

**Technical Research Scope:**

- Profiling methodology — tooling for two workload shapes: long-running stochastic CPU-bound GRASP search, and I/O-bound setup CLI (download + DEM/graph preparation); representative measurement of randomized runs; stage-level attribution
- Speed-up options within Python — vectorization, algorithmic wins, caching/incremental work in the setup pipeline, Numba/Cython as intermediate steps
- Rust hot-loop extraction — PyO3/maturin hybrid, framed for a Rust beginner; expected gains for GRASP-style graph search; ecosystem maturity
- Realistic expectations — reported speed-up ranges for comparable Python→native extractions; where I/O-bound work caps gains

**Out of scope:** full Rust rewrite (unless findings show no extractable hot core); the setup CLI progress-reporting gap (noted as context only — instrumentation seams overlap, fix tracked separately)

**Research Methodology:**

- Current web data with rigorous source verification
- Multi-source validation for critical technical claims
- Confidence level framework for uncertain information

**Scope Confirmed:** 2026-07-02

---

## Technology Stack Analysis

Context: steeproute's current stack (from `pyproject.toml`) is Python ≥3.13 with networkx/osmnx (graph + OSM download), rasterio + numpy (DEM), shapely (geometry), click (CLI). Two workloads: the CPU-bound GRASP solver and the I/O-plus-CPU setup pipeline (`steeproute-setup`).

### Profiling Tooling

The 2025–2026 consensus workflow for CPU-bound Python is a two-tool combination:

- **py-spy** — sampling profiler written in Rust; attaches to a *running* process by PID with no code changes and near-zero overhead, records flamegraphs (`py-spy record`), and shows live top-like output (`py-spy top`). Ideal for the long-running GRASP search: attach mid-run, sample 30–60 s, get a representative flamegraph of the steady-state hot path. ([py-spy GitHub](https://github.com/benfred/py-spy))
- **Scalene** — line-level profiler that splits time into **Python vs native vs system (I/O)** and profiles memory. The Python/native split is exactly the signal needed to decide whether hot code is already spending its time inside numpy/GDAL C code (native extension won't help) or in interpreted Python (big win available). ([Scalene GitHub](https://github.com/plasma-umass/scalene), [comparison](https://johal.in/profiling-scalene-py-spy-memory-cpu-flamegraphs-2025/))
- cProfile/line_profiler remain useful for deterministic function-call counts in unit-sized experiments, but sampling profilers are preferred for whole-run realism (no per-call overhead distorting tight loops).

For the setup CLI, Scalene's system-time attribution (or even coarse stage-level wall-clock instrumentation) distinguishes network wait from CPU work — the decisive first split for that pipeline. _Confidence: high — multiple current sources agree on the py-spy + Scalene pairing._

### Graph Libraries — the pre-built Rust escape hatch

**rustworkx** (Rust core, Python API, from the Qiskit team) is the standout finding for the solver side:

- Reported speedups of **3x–100x over networkx** depending on how much work is offloaded; an independent 2025 academic comparison measured betweenness centrality at 3.14 ms vs networkx's 113 s on the same dense network. ([rustworkx benchmarks](https://www.rustworkx.org/benchmarks.html), [JOSS paper](https://www.theoj.org/joss-papers/joss.03968/10.21105.joss.03968.pdf), [Springer SNAM 2025](https://link.springer.com/article/10.1007/s13278-025-01409-y))
- **Not a drop-in replacement**: integer node indices instead of hashable node objects, different API shapes; migration means touching every graph call site. Large projects (e.g. dbt-core) have run this exact migration discussion. ([dbt-core discussion](https://github.com/dbt-labs/dbt-core/discussions/9504))
- Relevance: if profiling shows time concentrated in networkx traversal/shortest-path calls, rustworkx captures most of the "Rust speedup" **without writing any Rust** — which reframes the hot-loop-extraction question. igraph and graph-tool are comparable-speed C/C++ alternatives, but rustworkx's API is closest to networkx idioms.

### Native-Extension Frameworks (the Rust hot-loop path)

- **PyO3 + maturin** is the standard, mature toolchain for Rust extensions (used by Polars, Ruff, Pydantic v2, orjson, HF tokenizers). `maturin develop --release` gives an edit-compile-import loop inside a uv/pip project. ([PyO3 guide](https://pyo3.rs/), [maturin tutorial](https://www.maturin.rs/tutorial.html))
- Documented hot-loop extractions report gains from **~3x** (straightforward port of already-numpy-friendly code) to **~100x** (pure-Python object-churning inner loops) — the canonical write-up is "Making Python 100x faster with less than 100 lines of Rust". ([ohadravid.github.io](https://ohadravid.github.io/posts/2023-03-rusty-python/), [pythonspeed.com](https://pythonspeed.com/articles/intro-rust-python-extensions/)) _Confidence: medium on specific multipliers — workload-dependent; high on the pattern._
- **Known pitfall #1:** calling a Rust function *inside* a tight Python loop — boundary-crossing overhead eats the gain. The extraction unit must be "one call, whole batch of work inside Rust." This constrains *where* in the GRASP iteration structure a cut is viable.
- **Known pitfall #2:** benchmarking debug builds (10–20x slower than `--release`) produces false negatives.
- **Alternatives short of Rust:** Numba (decorator JIT, easiest adoption, best on numeric array loops; free-threading support still experimental as of 0.63) and Cython 3.x (incremental — type the hot 5%; mature, but C-flavored tooling). Comparative 2025/2026 benchmarks put both Cython and Rust at roughly similar ceilings (~100x on compute-bound loops) with Numba close behind when its JIT fits the code shape. ([softwarelogic.co benchmark](https://softwarelogic.co/en/blog/python-optimization-showdown-is-numba-or-cython-faster), [2026 comparison](https://www.cloudcontraptions.com/blog/high-performance-python-choosing-2026/), [Cython vs PyO3](https://docs.bswen.com/blog/2026-03-10-cython-vs-rust-pyo3-python-performance/)) _Confidence: medium — vendor/blog benchmarks, but directionally consistent across sources._

### Python Runtime Itself

- **Python 3.14** free-threaded build: single-thread penalty is down to ~5–10% (from ~40% in 3.13), and multi-threaded CPU-bound workloads report 2–4x on 4 cores. Relevant because GRASP restarts are embarrassingly parallel — free-threaded 3.14 (or plain multiprocessing today) parallelizes seeds/restarts with zero Rust. ([miguelgrinberg.com](https://blog.miguelgrinberg.com/post/python-3-14-is-here-how-fast-is-it), [danilchenko.dev benchmarks](https://www.danilchenko.dev/posts/python-314-free-threading/))
- The 3.14 copy-and-patch **JIT** shows 10–30% on some CPU-bound paths but "no significant gains" in other credible tests — not a strategy, just a possible free upgrade. _Confidence: low-medium — sources conflict._ ([krun.pro](https://krun.pro/python-jit/), [Grinberg](https://blog.miguelgrinberg.com/post/python-3-14-is-here-how-fast-is-it))
- _Decision note (2026-07-02): Yann confirmed migrating steeproute to Python 3.14 is on the table if it offers useful functionality — treat 3.14 features as available options, not future speculation._

### Setup-Pipeline Stack (download + preparation)

- **osmnx** caches Overpass HTTP responses locally by default (`settings.cache_folder`); repeated setups over the same/overlapping areas should hit cache, and `cache_only_mode` supports download-once-build-many workflows. Overpass itself is rate-limited and single-request-serial — network wait is likely irreducible on first download, making *progress visibility* (the known v1 gap) more valuable than raw speedup there. ([OSMnx docs](https://osmnx.readthedocs.io/en/stable/getting-started.html), [user reference](https://osmnx.readthedocs.io/en/stable/user-reference.html))
- **rasterio**: windowed reads matching the dataset's internal block structure are the standard technique for large rasters; GDAL releases the GIL during raster I/O and numpy releases it in ufuncs, so thread-pool concurrency over windows gives near-linear scaling (~4x on 4 threads) for window-parallel raster work. ([rasterio concurrency docs](https://rasterio.readthedocs.io/en/stable/topics/concurrency.html), [windowed R/W docs](https://rasterio.readthedocs.io/en/stable/topics/windowed-rw.html))
- The IGN WMS DEM tile download (`pipeline/dem_download.py`) is a plain HTTP fetch loop — candidate for concurrent requests and/or tile-level caching, subject to IGN service rate limits.

### Technology Adoption Trends

- The dominant 2025–2026 pattern for Python performance is **"keep Python as orchestrator, compile the hot 5%"** — via a pre-built Rust-core library where one exists (polars, rustworkx, ruff) or a small PyO3 extension where one doesn't. Full rewrites are rare and reserved for cases where the whole program is the hot path.
- Profile-first is universally emphasized: every credible source warns against choosing the acceleration technology before measuring. ([pythonspeed.com](https://pythonspeed.com/articles/intro-rust-python-extensions/), [nandann.com](https://www.nandann.com/blog/rust-pyo3-python-extensions-guide))

---

## Integration Patterns Analysis

How each candidate technology actually bolts onto steeproute's uv-managed, Windows-developed Python project. (The generic API/microservices framing of this step doesn't apply — "integration" here means build-system, data-boundary, and toolchain interop.)

### Rust Extension ↔ uv Project Integration

- **maturin is a PEP 517 build backend**, so a Rust extension integrates two ways: (a) convert steeproute itself to a mixed Python/Rust package with maturin as backend, or (b) — lower risk — keep steeproute's hatchling build untouched and add a **separate small extension package** (e.g. `steeproute-core`) built with maturin, consumed as a path/workspace dependency. Option (b) keeps the pure-Python install path working if the Rust toolchain is absent. ([maturin](https://www.maturin.rs/), [pydevtools tutorial](https://pydevtools.com/handbook/tutorial/build-a-python-library-with-a-rust-extension/))
- **uv and maturin interoperate**: `uv sync` compiles the Rust code and installs it into the venv; `uv run` triggers `maturin pep517 build-wheel` automatically. Known rough edge: `uv run` builds in **release mode with no easy debug-build toggle**, and rebuild-on-every-change friction is a tracked issue — the practical dev loop is `maturin develop --release` run manually. ([maturin#2314](https://github.com/PyO3/maturin/issues/2314), [uv docs](https://docs.astral.sh/uv/concepts/projects/init/)) _Confidence: high for the mechanism; medium for the current state of rough edges (fast-moving tooling)._
- **Environment-specific risk (this machine):** cargo/crates.io behind the corporate TLS-intercepting proxy is the same failure class as the known uv `--native-tls` flake — expect to configure cargo's TLS/CA handling before the first build works. Windows also needs the MSVC Build Tools for the Rust toolchain. _Confidence: high that setup friction exists; specifics need a spike._

### Python ↔ Rust Data Boundary

- **rust-numpy (PyO3 ecosystem) gives zero-copy access to numpy arrays**: `PyReadonlyArray1<f64>` → `as_slice()` borrows the buffer without copying (errors if non-contiguous); results can return as numpy arrays owning a Rust-allocated buffer — one allocation, no intermediate copies. ([rust-numpy](https://github.com/PyO3/rust-numpy), [copy-avoidance discussion](https://github.com/PyO3/rust-numpy/discussions/432))
- The established boundary design: **pass flat arrays, not Python objects**. For graph work that means handing the graph over once in CSR-style form (index arrays + edge-attribute arrays like slope/length) and keeping the search loop entirely inside Rust — "move pointers, not payloads; bulk operations, not scalars." ([maxwellrules.com](https://maxwellrules.com/programming/rusty-python.html))
- This constrains extraction granularity: a viable cut is "score/expand N candidate paths" or "run one full GRASP construction+local-search iteration," not "evaluate one edge" — the latter dies on boundary-crossing overhead (consistent with the step-2 pitfall finding).

### rustworkx ↔ networkx Interop

- **`rustworkx.networkx_converter(graph, keep_attributes=True)`** converts an existing networkx graph directly; node payloads keep original attributes plus a `__networkx_node__` key holding the original node id. networkx stays installed — the converter is a bridge, not a replacement requirement. ([converter API](https://www.rustworkx.org/apiref/rustworkx.networkx_converter.html), [migration guide](https://www.rustworkx.org/networkx.html))
- Core migration cost: rustworkx addresses nodes by **integer index** returned at add-time, not by hashable object. steeproute's osmnx graph uses OSM node ids as keys, so an id↔index mapping must live somewhere — either maintained alongside a converted graph at query time, or baked in at setup time (store the graph in index form + mapping table).
- Integration shape worth noting: osmnx builds a networkx graph natively, so the cheapest pattern is **networkx for build/setup (unchanged), convert once, rustworkx for the query-time hot path** — conversion cost is paid once per run, amortized over ~200k GRASP iterations.

### Profiling-Tool Platform Integration

- **py-spy fully supports Windows** (uses `ReadProcessMemory`); attach-by-PID and flamegraph recording work natively — it can attach to a live `steeproute` GRASP run mid-search. ([py-spy](https://github.com/benfred/py-spy))
- **Scalene's Windows support is limited; full functionality effectively requires WSL2.** This downgrades the step-2 "py-spy + Scalene" pairing on this machine: py-spy natively, Scalene under WSL2 only if the Python/native/I/O split proves necessary (steeproute has no Windows-only dependencies that would block a WSL2 run). ([Scalene](https://github.com/plasma-umass/scalene)) _Confidence: high._
- Practical implication: the first-pass profiling loop is py-spy flamegraphs + coarse stage-level `time.perf_counter()` instrumentation in the setup CLI — the same instrumentation seams the future progress-reporting fix needs (stage boundaries: OSM download → DEM download → mosaic → graph build → enrichment).

### Parallelism Integration (GRASP restarts)

- **Windows only supports `spawn`**: every worker process re-imports the module and receives arguments by pickle — no copy-on-write fork. Shipping the full enriched graph to each worker per task is the main cost; the standard mitigation is a **long-lived `Pool` with an `initializer`** that loads/builds the graph once per worker, plus module-top-level worker functions (spawn's picklability rule). ([fork vs spawn](https://medium.com/@Nexumo_/python-multiprocessing-revisited-fork-vs-spawn-5b9216fd5710), [pythonspeed on pickle overhead](https://pythonspeed.com/articles/faster-multiprocessing-pickle/))
- Since workers only need the graph read-only, per-worker loading from the cached setup artifacts (each worker deserializes from disk in its initializer) sidesteps inter-process transfer entirely.
- Python 3.14 free-threading (now migration-eligible per scope note) would replace this with plain threads sharing one graph object — but library compatibility must be verified first: C-extension deps (rasterio/GDAL, shapely, numpy) each need free-threaded wheels; numpy and shapely are ready, rasterio/GDAL status needs checking at decision time. _Confidence: medium — ecosystem status moves monthly._

---

## Architectural Patterns and Design

How to structure the code so profiling, optimization, and a possible native core stay cheap and safe. (Generic microservices/cloud patterns don't apply — the relevant architecture is intra-process.)

### Kernel-and-Shell (the proven Rust-core shape)

The dominant production pattern is a **thin native kernel behind a stable Python API**:

- **pydantic v2** splits into `pydantic` (Python: model definition, API) and `pydantic-core` (Rust: validation/serialization hot path) — two packages, one conceptual product. ([Pydantic architecture docs](https://docs.pydantic.dev/latest/internals/architecture/))
- **polars** keeps a `py-polars` bindings crate deliberately thin over the real Rust crate; design tenets are zero-copy data layout at the boundary and parallelism (Rayon) *inside* the core, not across the boundary. ([polars GitHub](https://github.com/pola-rs/polars), [bridging pattern write-up](https://colliery.io/blog/rust-python-pattern/))
- Applied to steeproute: the candidate kernel is the GRASP inner iteration (construction + local search over the prepared graph), exposed as one batch-level function; click CLI, config, validation, report generation all stay Python. This matches the separate-`steeproute-core`-package integration shape from the previous section.

### Extract-Interface-First (do this before any Rust)

A refactor-only preparatory pattern, independent of whether Rust ever happens: **isolate the hot kernel behind an explicit interface with flat-array inputs while still in pure Python**. Benefits, in order of certainty:

1. Profiling gets sharper (the kernel becomes one obvious frame in the flamegraph).
2. Pure-Python optimizations (vectorization, better data structures) happen behind the same interface.
3. If Rust extraction proceeds, the boundary and its tests already exist — the port is a re-implementation, not a redesign.

This sequences the learning-value goal too: the Rust step becomes "implement a well-specified, well-tested function in a new language," the gentlest realistic first Rust project.

### Parity Architecture: Differential Testing Against the Python Reference

For any re-implementation (Rust kernel, rustworkx migration, or aggressive vectorization), the established correctness pattern is **differential testing** — keep the original Python implementation as the oracle and assert equivalent outputs over many generated inputs. ([differential-testing literature](https://arxiv.org/pdf/2102.07498), [compiler-verification experience paper](https://arxiv.org/pdf/2212.01748))

steeproute-specific wrinkle — **RNG determinism**: the pinned regression goldens (Epic 8/9 discipline) implicitly pin the RNG consumption sequence. Any port changes that sequence unless the RNG is reproduced bit-exactly. Two architectural options:

- **Bit-parity**: implement the same PRNG (e.g. port Python's Mersenne Twister usage or switch both sides to a shared explicit PRNG like PCG64 with identical draw order). Enables golden-identical differential tests, but couples implementations tightly. _Verdict: high effort, brittle — draw-order coupling breaks on any refactor._
- **Statistical parity + golden rebake**: assert the new kernel produces routes of equivalent *quality distribution* over a seed set (and identical results for the deterministic sub-components: scoring, feasibility checks), then rebake goldens once at migration time — the same reconciliation move already exercised in Story 9.3. Benchmarking literature supports multi-seed distributional comparison over single-seed identity, which also avoids the documented **seed over-tuning pitfall** (optimizing until one blessed seed looks good). ([algorithm-configuration pitfalls](https://arxiv.org/pdf/1705.06058), [restart-fair benchmarking protocol](https://arxiv.org/pdf/2509.08986)) _Verdict: recommended — aligns with existing project practice._
- Either way, the *deterministic* parts of the kernel (edge scoring, constraint checks, path geometry) should get exact differential tests; only the stochastic search outcome gets distributional treatment.

### Performance-Regression Harness

Two credible tools, different shapes:

- **pytest-benchmark** — benchmarks as pytest tests, JSON export, `--benchmark-compare` against saved baselines. ([bencher.dev guide](https://bencher.dev/learn/benchmarking/python/pytest-benchmark/))
- _Decision (Yann, 2026-07-02):_ performance tests are a **dedicated suite** (e.g. `tests/benchmarks/`), not benchmark assertions added to existing unit/e2e tests — the current tests verify functionality and stay that way. pytest-benchmark still fits as the harness for that dedicated suite; a `benchmark` marker (or separate testpath) keeps it out of the default run, same exclusion pattern as `live`/`slow`. Benchmark fixtures are sized for measurement (representative graph, fixed seed/params), independent of the functional fixtures.
- **asv (airspeed velocity)** — benchmarks the project *across its git history*: automatic statistically-significant-regression detection, HTML trend reports, `asv find` bisection; what SciPy uses. Heavier setup and a separate benchmark suite/config. ([asv docs](https://asv.readthedocs.io/en/stable/using.html), [SciPy benchmarking guide](https://docs.scipy.org/doc/scipy/dev/contributor/benchmarking.html))
- For a personal project with an existing pytest discipline, **pytest-benchmark first** is the low-friction fit; asv is justified mainly if long-horizon trend tracking becomes interesting in itself (a valid learning-value pick, but not needed for the tuning decision). _Confidence: high._
- Measurement design for a stochastic solver: benchmark **time per N iterations at fixed seed and fixed params** (throughput), separately from **route quality at fixed budget** (the existing goldens' job). Mixing the two in one metric makes both noisy — supported by the fixed-time/restart-fair benchmarking literature above.

### Setup-Pipeline Architecture (staged, cache-bounded, observable)

- The pipeline already has natural stage boundaries (OSM download → DEM download → mosaic → graph build → enrichment) with cached artifacts between some of them. The architectural upgrades the research supports: make every stage **idempotent and skippable when its artifact exists** (osmnx's HTTP cache provides this for free on its stage if enabled — verify `settings.use_cache` is on and the cache dir is persistent under `platformdirs`), and give each stage a **timing + progress seam** — one decorator/context-manager reused for both the profiling need (this research) and the progress-reporting fix (v1 gap).
- Within the DEM stages, the raster-processing patterns from the integration section apply behind those same seams (windowed, block-aligned, optionally thread-parallel — GIL released during GDAL I/O and numpy ufuncs). ([rasterio concurrency docs](https://rasterio.readthedocs.io/en/stable/topics/concurrency.html))

---

## Implementation Approaches and Technology Adoption

### Adoption Strategy: Measure → Cheap Wins → Decide

The consistent pattern across migration case studies: profile first, port narrowly, keep Python as the shell. Typically 10–20% of code consumes 80%+ of execution time; those hotspots are the only migration candidates. ([corrode migration guide](https://corrode.dev/learn/migration-guides/python-to-rust/), [incremental port case study](https://blog.waleedkhan.name/port-python-to-rust/)) Big-bang rewrites appear in the literature mainly as cautionary tales; the incremental PyO3 path reports good ergonomics and no interop segfaults in practice.

### A Sobering Finding: the Vectorization Ceiling for Graph Search

Best-first / priority-queue graph search is **inherently sequential** — per-step conditionals, dynamic frontier, ragged adjacency — and does not vectorize well in numpy. Dense-matrix reformulations waste O(N²) memory on sparse graphs. ([vectorizing graph algorithms essay](https://www.moderndescartes.com/essays/vectorized_pagerank/), [graph-based search evaluation](https://arxiv.org/html/2502.05575v2))

Implication for GRASP: the *search skeleton* (candidate-list construction loop, local-search moves) likely can't be vectorized away in pure Python. What *can* be vectorized is **batch scoring** — evaluating all candidate edges/moves of one step as numpy array ops over pre-flattened edge attributes. If profiling shows scoring dominates, numpy-batch scoring may suffice; if the loop skeleton itself dominates, the realistic options are rustworkx (if the time is inside its algorithms) or a native kernel — not more numpy. _Confidence: high on the principle; which case applies is exactly what profiling must answer._

### Rust Skill Acquisition (honest sizing for a Rust beginner)

- General Rust productivity from a Python background: **3–6 months of regular use**, with the steepest climb (borrow-checker fights) in the first month. ([corrode learning-curve guide](https://corrode.dev/blog/flattening-rusts-learning-curve/), [learning-curve resources](https://ntietz.com/blog/rust-resources-learning-curve/))
- But the scoped task here — implement **one well-specified, differentially-tested function** over flat arrays, no async, no lifetimes beyond borrowing input slices — is far below general productivity. Anecdotal reports of meaningful contributions after a couple of focused weekends exist for similarly narrow scopes ([HN thread](https://news.ycombinator.com/item?id=34691580)); a hobby-cadence estimate of a few weekends to a first working kernel, plus ongoing polish, is reasonable. _Confidence: medium — learning-speed anecdotes vary widely._ Per project practice, treat this as a design target, not a commitment.
- The extract-interface-first pattern (architecture section) is what makes the scoped-down estimate credible: the Rust work starts with a frozen spec and a test oracle already in place.

### Development Workflow (concrete loop per phase)

- **Profiling loop:** `py-spy record -o profile.svg --pid <pid>` against a live GRASP run (or `py-spy record -- uv run steeproute ...` for whole-run capture); stage-level `perf_counter` seams in the setup CLI. Scalene under WSL2 only if the Python-vs-native split is ambiguous from flamegraphs.
- **Benchmark loop:** dedicated `tests/benchmarks/` suite (decision recorded above) with pytest-benchmark; `--benchmark-autosave` + `--benchmark-compare` around every optimization commit; throughput metric = seconds per 1k GRASP iterations at fixed seed/params on the grenoble_small fixture graph.
- **Rust loop (if reached):** separate `steeproute-core` crate; `maturin develop --release` for iteration (uv handles it on `uv sync` too, but release-only — fine here since debug-build benchmarking is a known false-negative trap); differential tests run in the normal pytest suite against the Python reference kernel.

### Risk Register

| Risk | Exposure | Mitigation |
|---|---|---|
| Optimizing before measuring | Wasted weekends on non-bottlenecks | Phase order is non-negotiable: no optimization work before flamegraphs exist |
| Corporate proxy blocks cargo/crates.io | Rust path stalls at toolchain setup | Time-boxed setup spike before committing to the Rust phase; known-good pattern from the uv `--native-tls` fix |
| Boundary-crossing overhead eats Rust gains | Disappointing speedup despite effort | Batch-level API design (one call per iteration/batch), enforced by the extract-interface-first refactor |
| Golden churn from RNG divergence | Regression suite loses meaning | Statistical-parity + one-time rebake strategy (architecture section); deterministic sub-components keep exact tests |
| Free-threading ecosystem gaps (rasterio/GDAL) | 3.14t migration blocked for setup pipeline | Solver process doesn't import rasterio — free-threading can apply to the solver even if setup stays on the GIL build; verify import graph before assuming |
| Rust learning stall (motivation, hobby cadence) | Half-migrated kernel | Python reference kernel stays canonical until the Rust kernel passes parity + beats it in benchmarks; deleting the experiment costs nothing |

## Technical Research Recommendations

### Implementation Roadmap (phased, each phase independently valuable)

- **Phase 0 — Instrument (small):** stage-level timing seams in `steeproute-setup` (shared with the progress-reporting fix); verify osmnx HTTP cache is enabled and persistent. Deliverable: a per-stage time breakdown of a real setup run.
- **Phase 1 — Profile (small):** py-spy flamegraphs of a realistic GRASP run (200k-iteration params, grenoble-scale area) + the Phase 0 setup breakdown. Deliverable: ranked bottleneck list with Python-vs-native attribution.
- **Phase 2 — Benchmark harness (small):** dedicated `tests/benchmarks/` pytest-benchmark suite pinning throughput baselines before anything changes.
- **Phase 3 — Cheap wins (medium):** whatever Phase 1 indicts — numpy-batch scoring, data-structure fixes, setup-stage caching/concurrency (DEM tile fetch, windowed raster ops), parallel restarts via worker pool. Re-benchmark after each.
- **Phase 4 — Native kernel (large, conditional):** only if Phase 3 plateaus short of the (to-be-defined) target. Order of preference: **rustworkx migration** if time sits in graph-algorithm calls (Rust speed, no Rust authorship); **PyO3 `steeproute-core` kernel** if time sits in the bespoke GRASP loop (the learning-value option, de-risked by extract-interface-first); full rewrite remains out of scope per the confirmed research constraint.

### Technology Stack Recommendations

- Profiling: **py-spy** (native Windows) + stage seams; Scalene/WSL2 as backup. Benchmarks: **pytest-benchmark**, dedicated suite.
- Acceleration ladder: numpy batch ops → rustworkx → PyO3+maturin+rust-numpy. Numba/Cython acknowledged but not preferred here: Numba's JIT fits array loops better than object-heavy graph code and its free-threading support trails, while Cython adds a C-flavored toolchain without the learning-value payoff Rust carries for this project.
- Runtime: Python 3.14 migration is on the table (confirmed); treat the free-threaded build as a solver-side parallelism option to evaluate, the JIT as a free maybe.

### Skill Development

One-time investments, roughly in order: reading flamegraphs (hours), pytest-benchmark usage (hours), PyO3/maturin basics + enough Rust for slice-in/slice-out kernels (a few weekends to first parity-passing kernel, medium confidence), cargo-on-corporate-proxy setup (time-boxed spike).

### Success Metrics

- Primary: **seconds per 1k GRASP iterations** (fixed seed/params/fixture) and **wall-clock setup time** per stage on a reference area — both tracked by the benchmark suite from Phase 2 onward.
- Guardrail: pinned regression goldens stay green (or get one documented rebake at native-kernel parity, per the architecture decision).
- Qualitative (learning goal): a working, tested PyO3 extension authored end-to-end — valid success even if rustworkx ends up carrying the perf win.

---

## Research Synthesis

### Executive Summary

steeproute's performance question decomposes into two independent problems with different physics. The **setup pipeline** is dominated by network I/O (Overpass, IGN WMS) plus raster CPU work — native code buys little there; caching, concurrency, and visibility (the missing progress reporting) are the levers. The **GRASP solver** is a CPU-bound interpreted loop — exactly the workload where the 2025–2026 Python ecosystem offers a well-worn ladder: numpy batch scoring → a Rust-core library (rustworkx) → a small PyO3 kernel.

The research's most decision-relevant finding is the **vectorization ceiling**: priority-queue search skeletons don't vectorize, only batch scoring does. That reduces the entire strategy to one empirical question profiling must answer: *where inside the GRASP iteration does the time go?*

- **Scoring/feasibility math dominates** → numpy batching likely suffices; no new language.
- **Graph-algorithm calls (networkx) dominate** → migrate the query path to **rustworkx** via its official converter: 3x–100x reported, zero Rust authorship.
- **The bespoke loop skeleton dominates** → extract-interface-first, then a **PyO3 `steeproute-core` kernel** — the learning-value path, realistically a few weekends to a first parity-passing kernel because the boundary and test oracle exist before any Rust is written.

Everything before that decision is cheap and no-regret: stage timing seams in setup (shared with the progress fix), py-spy flamegraphs (native on Windows; Scalene needs WSL2), and a dedicated `tests/benchmarks/` pytest-benchmark suite pinning baselines. Golden-file safety is resolved by exact differential tests for deterministic components plus distributional quality comparison and a one-time rebake for the stochastic search — the Story 9.3 reconciliation pattern, avoiding brittle bit-exact RNG parity.

### Key Decisions Recorded During Research

- Python 3.14 migration is on the table (free-threading relevant to parallel restarts; solver process may dodge the rasterio/GDAL wheel question entirely — verify import graph).
- Performance tests live in a dedicated suite, not inside existing functional tests.
- Full Rust rewrite: out of scope. Numba/Cython: acknowledged, not preferred (fit and learning-value reasons, see implementation section).

### Next Steps

1. Phases 0–2 (instrument, profile, benchmark harness) are small, sequential, and independently valuable — natural candidates for promotion into an epic/stories when picked up, with the progress-reporting fix riding the same instrumentation seams.
2. Phase 3 (cheap wins) scope is unknowable until the flamegraphs exist — plan it after Phase 1 delivers the ranked bottleneck list.
3. Phase 4 (native kernel) is conditional; before committing, run the time-boxed cargo-behind-corporate-proxy spike.
4. Update `future-ideas.md` to point the *Performance tuning* item at this report.

### Methodology and Source Quality

Five research passes, twelve web searches, ~35 distinct sources: official docs (PyO3, maturin, rustworkx, rasterio, OSMnx, asv, Pydantic), peer-reviewed/JOSS material (rustworkx paper, Springer SNAM 2025 benchmark, algorithm-configuration and benchmarking-methodology papers), and practitioner accounts (pythonspeed, corrode, incremental-port case studies). Claims carry inline confidence levels; the weakest evidence class is blog-sourced speed-up multipliers (directionally consistent, precision low) and fast-moving ecosystem status (uv/maturin rough edges, free-threaded wheel coverage — re-verify at decision time). Numbers like "3x–100x" are ranges of reported cases, not predictions for steeproute; the benchmark suite exists to replace them with local measurements.

**Research completed:** 2026-07-02 · All five workflow steps executed with user scope confirmation at each gate.
