# Story 1.2: Runnable app skeleton with static shell

Status: done

<!-- App track (epics-app.md). Story key `app-1-2-*` is `app-`-prefixed to avoid
     collision with the CLI track's `1-2-*`; both share sprint-status.yaml. -->

## Story

As a developer,
I want a minimal runnable FastAPI app with the `steeproute-app` entry point and a static frontend shell,
so that there is a working, serve-able foundation to hang the job runner and screens on.

## Acceptance Criteria

1. `fastapi[standard]` is added to the project's runtime dependencies (via `uv add`), and a new `steeproute.app` subpackage exists at `src/steeproute/app/` with at least `__init__.py` and `main.py`.
2. `main.py` exposes a FastAPI factory + a `lifespan` (empty/placeholder is fine — no worker yet) and a `run()` callable registered as `[project.scripts] steeproute-app = "steeproute.app.main:run"`; `uv run steeproute-app` starts a single-process uvicorn server, and `uv run fastapi dev src/steeproute/app/main.py` starts it with hot reload.
3. Loading the home page serves a placeholder page that renders the persistent **global header**: app name → Map home, a **Runs** link → Run library, and a compact **live-job-indicator slot** that is present in the markup but empty for now (wired to SSE in a later story).
4. The frontend static directory (`src/steeproute/app/static/`) is served via `StaticFiles`; the placeholder home page and its CSS load from it (no inline-only page).
5. Leaflet 1.9.4 assets are served by mounting the copy **already vendored by the CLI report** (`src/steeproute/templates/assets/leaflet-1.9.4.min.{js,css}`) — reached at runtime via `importlib.resources`, no CDN reference, no new asset dependency added.
6. `src/steeproute/app/static/**` ships as package data — verified by a wheel build containing the static files (same default mechanism that already ships `templates/assets/**`).
7. Scope guard: this story creates **only** the skeleton — no `api.py`/`store.py`/`queue.py`/`sse.py`/`cli_adapter/`, no job endpoints, no progress model. The only routes are the static mounts + the home page.

## Tasks / Subtasks

- [x] Add the dependency and package (AC: #1)
  - [x] `uv add "fastapi[standard]" --native-tls` → `fastapi 0.139.0` + `uvicorn 0.51.0` (used `--native-tls` for the corporate-TLS network; `uv sync --native-tls` after to clear the stale editable build per AGENTS.md).
  - [x] Created `src/steeproute/app/__init__.py` and `src/steeproute/app/main.py`.
- [x] Wire the FastAPI app + entry point (AC: #2)
  - [x] `main.py`: `create_app()` factory returning `FastAPI(lifespan=...)`, a placeholder `lifespan` (logs start/stop; no worker yet), a module-level `app` for `fastapi dev`, and `run()` launching `uvicorn.run(..., workers=1)`. Root route via a module-level `APIRouter` (avoids the nested-handler `reportUnusedFunction`).
  - [x] Registered `[project.scripts] steeproute-app = "steeproute.app.main:run"`.
  - [x] Verified both entry points serve the home page (200): `uv run steeproute-app` and `uv run fastapi dev … --port …` (see Debug Log re: `fastapi dev` + UTF-8 on Windows).
- [x] Build the static shell (AC: #3, #4)
  - [x] `static/index.html` — placeholder Map-home body + global header (app-name → `/`, Map + Runs nav, empty `#live-indicator` slot).
  - [x] `static/css/app.css` — minimal hand-rolled header styling (no design system).
  - [x] Mounted `static/` via `StaticFiles` at `/static`; `index.html` served at `/` via a root route (`FileResponse`).
- [x] Serve the reused Leaflet assets (AC: #5)
  - [x] Mounted the CLI's vendored assets dir (resolved via `importlib.resources.files("steeproute") / "templates" / "assets"`) at `/vendor`; home page references `/vendor/leaflet-1.9.4.min.{js,css}` — no CDN.
- [x] Verify packaging + regression surface (AC: #6, #7)
  - [x] `uv build --wheel` → wheel contains `steeproute/app/static/index.html` + `.../css/app.css` (default hatchling mechanism, no extra config).
  - [x] `uv run basedpyright src/steeproute/app` → 0 errors/0 warnings; smoke test added; full suite green.
  - [x] `git status` confirms `src/` changes are only the new `src/steeproute/app/` — no edits to existing CLI source.

## Dev Notes

This is the **first App implementation story** (the spike, Story 1.1, wrote only fixtures — no `src/steeproute/app/` yet). Build the runnable shell and nothing more; every later component (job store, single-worker queue, subprocess spawn, progress classifier, SSE) hangs off this in Stories 1.3–1.6 [Source: architecture-app.md#Decision Impact Analysis — Implementation sequence].

**Authoritative package location — resolve a doc discrepancy.** The App is a **subpackage inside the existing distribution: `src/steeproute/app/`** (package path `steeproute.app`), NOT a top-level `steeproute_app`. The architecture's early "Starter Template Evaluation / Initialization" section shows `src/steeproute_app/main.py`; that is superseded by the later, authoritative "Project Structure & Boundaries" section, which places the App at `src/steeproute/app/` precisely so `cli_adapter` can do in-process read-only imports of `steeproute.cache` / `steeproute.cli._shared` later [Source: architecture-app.md#Project Structure & Boundaries]. Use `steeproute.app`. The epic AC agrees ("the `steeproute.app` package") [Source: epics-app.md#Story 1.2].

**Entry point is a uvicorn launcher, not the CLI click wrapper.** The two CLIs use `run_entry_point(...)` around a click command [Source: src/steeproute/cli/setup.py:279]. The App's `run()` is different — it launches uvicorn programmatically (single worker; concurrency=1 is a hard constraint per NFR1, though it only bites once the worker exists) [Source: architecture-app.md#Category 2; architecture-app.md#Development workflow]. Do not reuse the click harness here.

**Reuse Leaflet, don't re-vendor.** The CLI report already vendors Leaflet 1.9.4 (`src/steeproute/templates/assets/leaflet-1.9.4.min.{js,css}`); the App serves that same copy via a static mount [Source: architecture-app.md#Selected Starter; architecture-app.md#Complete project tree]. Resolve the dir at runtime with `importlib.resources.files("steeproute") / "templates" / "assets"` — the exact handle `output.py::_load_asset` uses [Source: src/steeproute/output.py:173-175]. 2.0 is still alpha — stay on 1.9.4. (Note: only `.js`/`.css` are vendored — no Leaflet marker PNGs; the actual map picker is Story 1.6, so this story needs only a mountable/reachable Leaflet, not a rendered map.)

**Verified current versions (pinned 2026-07 in the architecture, no re-research needed):** FastAPI `0.136.x` (install as `fastapi[standard]`, which bundles the server + `fastapi` CLI); Uvicorn `~0.42.0`; Leaflet `1.9.4` [Source: architecture-app.md#Selected Starter — Verified current versions]. FastAPI's native `EventSourceResponse` (SSE) and the API surface are later stories — do not add `sse-starlette` or any endpoints now [Source: architecture-app.md#Category 4, #Category 8].

**Conventions to honour even in the skeleton** [Source: architecture-app.md#Implementation Patterns & Consistency Rules]:
- **Frontend:** vanilla ES modules, no framework, no bundler (buildless). Frontend files kebab-case (`app.css`, later `map-home.js`); Python modules snake_case. When JS starts calling endpoints (Story 1.3+), a single `js/api.js` is the only file with URLs — not needed yet, but don't scatter hardcoded URLs into the shell.
- **Server logging vs. job progress are two distinct streams** — the server uses stdlib `logging` (operational); scraped CLI progress is data written elsewhere. No progress stream exists in this story; just don't conflate the two when you add any startup logging [Source: architecture-app.md#Process patterns].
- snake_case JSON / no response envelope / `status` Enum apply to the API and store — future stories, noted so the shell doesn't establish a contrary pattern.

**Packaging.** There is no explicit package-data config today; hatchling's wheel target (`packages = ["src/steeproute"]`) already ships non-Python files under the package (that is how `templates/assets/*` reach the wheel with zero config). Placing the frontend under `src/steeproute/app/static/` inherits the same mechanism — verify with `uv build`, add explicit `force-include`/`artifacts` config only if the build proves it necessary [Source: pyproject.toml:115-117; architecture-app.md#pyproject additions].

### Project Structure Notes

Target tree (this story creates the **starred** files only; the rest are later stories, shown for context) [Source: architecture-app.md#Complete project tree]:

```
src/steeproute/app/
├── __init__.py            ★
├── main.py                ★  FastAPI factory + lifespan (empty) + static mounts + run() entry point
├── api.py / models.py / store.py / queue.py / sse.py / cli_adapter/   (Stories 1.3–1.6, 2.x — NOT now)
└── static/                ★  buildless frontend (ships as package data)
    ├── index.html         ★  placeholder Map-home body + global header + empty live-indicator slot
    ├── css/app.css        ★
    └── js/                   (map-home.js, api.js, live-indicator.js, … — later stories)
```

- Global header is shared chrome (app name → Map home, Runs → Run library, live-indicator slot). The indicator subscribes to the active job's SSE in Story 1.5; here it is present-but-empty markup only [Source: epics-app.md#Story 1.2; ux-design-specification.md §2 Global chrome; architecture-app.md#Category 10].
- Flat navigation: Map home / Run library / active Run watch are the only top-level destinations; the header realizes the first two [Source: ux-design-specification.md §2].

### Testing

Per AGENTS.md: `uv run basedpyright <files>`; tests per-directory (never mix `tests/unit` and `tests/integration` in one invocation). App tests use FastAPI's `TestClient` [Source: architecture-app.md#Development workflow]. For this story add a minimal integration smoke test (e.g. `tests/integration/test_app_api.py`) asserting `GET /` returns 200 and the header markup (app name, Runs link, `#live-indicator` slot) is present, and that the Leaflet asset mount serves the vendored file. Keep it to the skeleton's surface — the full job-lifecycle API tests arrive with Stories 1.3+.

### References

- [Source: _bmad-output/planning-artifacts/epics-app.md#Story 1.2: Runnable app skeleton with static shell]
- [Source: _bmad-output/planning-artifacts/architecture-app.md#Project Structure & Boundaries] — authoritative `src/steeproute/app/` location + full tree + pyproject additions
- [Source: _bmad-output/planning-artifacts/architecture-app.md#Selected Starter: None — extend the existing uv project] — dependency add, verified versions, Leaflet reuse
- [Source: _bmad-output/planning-artifacts/architecture-app.md#Category 2 — Worker & concurrency] — single worker / concurrency=1 (entry-point launcher context)
- [Source: _bmad-output/planning-artifacts/architecture-app.md#Category 10 — Frontend architecture] and #Implementation Patterns & Consistency Rules — buildless frontend, header/live-indicator, naming
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md#2 Screen inventory — Global chrome]
- [Source: src/steeproute/output.py:173-175] — `importlib.resources` asset-path pattern for the Leaflet mount
- [Source: src/steeproute/cli/setup.py:279] — the CLI click entry-point pattern the App `run()` deliberately does NOT copy
- [Source: pyproject.toml:86-89, 115-117] — `[project.scripts]` block + hatchling wheel packaging
- [Source: _bmad-output/implementation-artifacts/app-1-1-stdout-format-inventory-spike.md] — prior story: fixtures only, no `app/` package yet

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Opus 4.8)

### Debug Log References

- **Resolved versions:** `fastapi 0.139.0`, `uvicorn 0.51.0`, `starlette 1.3.1` (architecture pinned `0.136.x`; the newer install still has native SSE, ≥0.135). Ran `uv add … --native-tls` + `uv sync --native-tls` on the corporate-TLS network; the sync also cleared the stale editable build (dev117 → dev122) so `steeproute-app.exe` registered and e2e subprocess tests stayed green.
- **`fastapi dev` + Windows console encoding:** `uv run fastapi dev …` crashed with a `rich`/`rich_toolkit` `UnicodeEncodeError` while printing its rocket-emoji banner **only when stdout was redirected to a file** (legacy cp1252 code page). Re-running with `PYTHONUTF8=1` served the home page (HTTP 200 in ~2 s). This is a `fastapi_cli` banner artifact under redirected output, not an app defect — an interactive terminal (UTF-8) is unaffected. `uv run steeproute-app` (the shipped entry point) has no such issue.
- **Pre-existing warning (not introduced here):** `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated` — emitted by FastAPI's `TestClient` import, harmless, surfaces once in every App test run.

### Completion Notes List

- Runnable FastAPI skeleton stood up: `create_app()` factory + placeholder `lifespan` + module-level `app` + `run()` uvicorn launcher, `steeproute-app` script registered. Verified live: `uv run steeproute-app` and `fastapi dev` both serve `/` (200), `/vendor/leaflet-1.9.4.min.{js,css}` (200), `/static/css/app.css` (200).
- Leaflet reused from the CLI's vendored copy via a `/vendor` static mount (`importlib.resources.files("steeproute")/templates/assets`) — no CDN, no new asset dependency. Frontend shell (`index.html` + `app.css`) renders the global header with app-name → Map home, Map/Runs nav, and an empty `#live-indicator` slot (SSE-wired in Story 1.5).
- Scope held to the skeleton (AC #7): no `api.py`/`store.py`/`queue.py`/`sse.py`/`cli_adapter/`, no job endpoints, no progress model. Server uses stdlib `logging` (operational) only.
- Packaging: wheel ships `steeproute/app/static/**` via hatchling's default package-data mechanism (same as `templates/assets/**`) — no `force-include`/`artifacts` config needed.
- **Doc note for downstream:** the `steeproute.app` package path (not `steeproute_app`) is authoritative, per architecture-app.md §Project Structure; the earlier §Initialization `steeproute_app` mention is superseded.
- Validation: `basedpyright src/steeproute/app` 0/0; `ruff` clean; full offline suite **917 passed, 17 deselected, 0 failures** (~105 s), including 5 new App smoke tests.

### File List

- `src/steeproute/app/__init__.py` (new) — subpackage docstring
- `src/steeproute/app/main.py` (new) — FastAPI factory, lifespan, static/vendor mounts, home route, `run()` entry point
- `src/steeproute/app/static/index.html` (new) — placeholder Map-home + global header
- `src/steeproute/app/static/css/app.css` (new) — minimal header styling
- `tests/integration/test_app_api.py` (new) — 5 smoke tests (factory, home HTML, header markup, static + vendored-Leaflet mounts)
- `pyproject.toml` (modified) — `fastapi[standard]` dep + `steeproute-app` script entry
- `uv.lock` (modified) — resolved FastAPI/uvicorn/starlette + transitive deps
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (modified) — story status tracking

## Change Log

| Date | Change |
|---|---|
| 2026-07-15 | Story drafted from epics-app.md + architecture-app.md. Status → ready-for-dev. |
| 2026-07-15 | Implemented runnable FastAPI skeleton + static shell + reused-Leaflet mounts + `steeproute-app` entry point; 5 smoke tests; full suite green (917). Status → review. |
| 2026-07-15 | Code review (low effort, diff pass): no findings. Status → done. |
