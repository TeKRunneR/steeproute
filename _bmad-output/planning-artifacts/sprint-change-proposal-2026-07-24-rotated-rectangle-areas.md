# Sprint Change Proposal — Rotated-Rectangle Search Areas

**Date:** 2026-07-24
**Author:** Yann (via correct-course)
**Trigger type:** New requirement emerged from hands-on use (post-v1 capability increment)
**Change scope classification:** **Moderate** (net-new epics on two tracks; backlog
reorganization; no rollback of shipped work)

---

## Section 1 — Issue Summary

The search area is a **centered square** everywhere: a single `Area.radius_km`
(a bbox half-side) drives an axis-aligned square OSM fetch. That shape is a poor
fit for the tool's actual target — whole mountain ranges, which are rarely
oriented N–S or E–W. **Belledonne runs SW–NE.** A north-aligned square (or even
an axis-aligned rectangle) over such a range necessarily encloses large wedges of
off-axis **valley** that the solver never uses (the `--theta` average-slope floor
rejects valley routes) but that **setup still fully pre-processes** — the
expensive, cache-once phase.

**Desired change:** let the search area be a **rotated rectangle** so it can hug a
diagonally-oriented range and exclude off-axis valleys from setup. Arbitrary
polygons are explicitly **out of scope** (not worth per-query map-drawing effort).
Projection fidelity is **not** required — an approximation-grade local frame is
acceptable, since the goal is "which area do I pre-process," not survey accuracy.

**Decisions taken at session start:**
- **Unified model, one increment.** Model the area as a rotated rectangle whose
  axis-aligned-rectangle and square forms fall out as the `angle = 0` /
  `equal-extents` cases. One `Area` generalization, one cache-schema bump, one
  envelope-audit pass — not the same disruptive work done twice.
- **Working mode:** Batch.

### Why the payoff is real (and where it's partial)

Per Epic 14's measured r20 breakdown, the dominant setup cost is the **per-vertex
CPU stages** — elevation sampling (~215 s), resampling (~62 s), smoothing
(~33 s), per-edge metrics, trail filter. These scale with the number of graph
vertices, i.e. with the **true area retained after truncation to the rotated
polygon** — so trimming off-axis valley shrinks them proportionally. OSM download
(Overpass) and DEM tile fetch are driven by the **axis-aligned bounding box** of
the rotated rectangle (both are bbox-oriented sources), so those shrink less. Net:
**large win on the CPU-bound majority of setup, partial win on ingestion/fetch.**
Honest, and still clearly worth it for a diagonal range.

---

## Section 2 — Impact Analysis

### Epic Impact

- **No completed epic is invalidated or rolled back.** v1 (CLI Epics 1–14, App
  Epics 1–4) is shipped and untouched. This is a post-v1 additive increment, in
  the same vein as CLI Epics 11–14 and App Epic 4.
- **Two net-new epics**, mirroring the existing two-track structure and honoring
  the CLI→App dependency (the App shells out to the CLI, so the engine must land
  first):
  - **CLI Epic 15 — Rotated-Rectangle Search Areas** (the engine).
  - **App Epic 5 — Rotated-Rectangle Map Selection** (the picker; depends on 15).
- Deferred stories (13.4/13.5, 14.6) are **unaffected** and stay parked.

### Artifact Conflicts

**CLI PRD (`prd.md`)**
- **FR1** ("area via center point and radius") — must generalize to a rectangle
  with optional rotation; radius remains a square shorthand.
- **FR2** (area-cap rejection) — the cap check currently computes a **disk** area
  (`π·radius²`) as a proxy; a rotated rectangle needs the **true** rectangle area
  (`2·half_width · 2·half_height`). Behavior-relevant, must change.
- **FR10** ("routes strictly contained within the specified search area") —
  containment now tests against the rotated polygon. `shapely.contains` is already
  orientation-agnostic, so the *check* is fine; the risk is the **bbox-envelope
  shortcuts** (see Technical Impact).
- **FR23** (setup takes the same area flags) — stays true by construction.

**CLI Architecture (`architecture.md`)**
- **Cat 3 (pipeline):** setup fetch moves from `osmnx.graph_from_point(dist_type="bbox")`
  to `graph_from_polygon(rotated_ring)`. This reuses osmnx's existing
  `truncate_graph_polygon` path (the bbox mode already goes bbox→polygon→truncate
  internally), so it's a natural fit, not a new mechanism.
- **Cat 4b (cache key):** `_canonicalize_area` gains a new area **mode** alongside
  `"center_radius"` (the code already anticipates this: *"A future polygon or
  named-region mode would dispatch here"*). New geometry fields must enter the
  canonical dict or two boxes differing only in angle/extent would collide.
- **Cat 4c (on-disk format) + manifest/index schema:** `manifest.json`'s `area`
  block and the index entries carry `{mode, center, radius_km}` today; adding
  extents+angle is a **schema-version bump** (existing entries re-prepare once, the
  established pattern from Stories 13.2/14.2).
- **Cat 5 (solver):** **explicitly unaffected** — the solver, validator, climb
  detection, and contraction never see box geometry (the graph *is* the box). Worth
  recording so a reader doesn't go looking.

**App track (`architecture-app.md`, `epics-app.md`, `ux-design-specification.md`)**
- **Cat 6 (`GET /regions`) / `RegionBounds`:** built-region overlays currently
  ship an axis-aligned four-corner bbox; a rotated region needs its true polygon
  (or an axis-aligned envelope with a documented over-report). Envelope-leak audit
  applies here too.
- **Cat 9 (params_schema / argv):** the argv builder hardcodes `--center`/`--radius`;
  it needs the new flags. `params_schema` deliberately excludes area fields (the
  map owns them), so the schema form is unaffected — this stays an argv-seam change.
- **Map picker (`js/map-home.js`, UX-DR1):** `L.rectangle` + single radius handle
  → `L.polygon` + a second dimension handle + a rotation handle; the move-selection
  mode's rigid axis-aligned translation generalizes; select-region snaps to the
  rotated geometry.

**Cross-cutting**
- **`epics.md` / `epics-app.md`:** add the two epics + FR-coverage rows.
- **`sprint-status.yaml`:** add `epic-15` (+3 stories) and `app-epic-5` (+2
  stories) as `backlog`.
- **`future-ideas.md`:** add a "Promoted 2026-07-24" note.
- **Regression goldens:** **no rebake expected.** The backward-compatible square
  path (existing `--center`/`--radius`) must produce a byte-identical fetch →
  identical graph → green goldens. This is a hard guardrail, not a hope. The new
  shape gets **its own** regression coverage per the AGENTS.md solver/golden
  policy (new behavior → new golden; existing goldens must not silently change).

### Technical Impact — the one real risk

Two mechanical concerns, one trivial and one an audit:

1. **Rotation math (trivial, ~10 LOC).** Rotating in raw lat/lon degree-space
   skews the box (1° lon ≠ 1° lat). Fix: lift the four corners into a local km
   frame (scale lon by `cos(lat)` — the codebase already has `_DEG_PER_KM_LAT` /
   `_deg_per_km_lon`), rotate there, convert back. Flat-earth is accurate to a
   fraction of a percent at range scale — invisible for "which valleys to fetch."

2. **Envelope-leak audit (the actual work).** Several consumers shortcut through
   `polygon.bounds` (the min/max envelope) as if it were "the region" —
   `area_bbox_wgs84` (`cache.py`), the App's `RegionBounds`, the render overlay
   (`output._search_bbox`). A rotated box's envelope is *larger* than the box, so
   coverage would over-report and overlays would draw too big. `shapely.contains`
   itself is fine; it's these shortcut sites that need finding and fixing. This
   audit is identical work regardless of rotated-rect vs. arbitrary-polygon, and is
   the single item most likely to hide a bug.

---

## Section 3 — Recommended Approach

**Option 1 — Direct Adjustment (add net-new epics within the existing plan).**
- **Effort:** Medium. **Risk:** Low–Medium (the envelope audit is the risk; the
  rotation approx and schema bump are well-trodden patterns here).
- **Rationale:** v1 is shipped; there is nothing to roll back and no MVP to
  re-scope. The change is a clean additive capability that fits the established
  post-v1 correct-course cadence and the two-track epic structure. Rejected
  alternatives: **Rollback** (N/A — no shipped work conflicts) and **MVP Review**
  (N/A — MVP delivered long ago).

**Sequencing:** CLI Epic 15 fully, then App Epic 5. The App cannot expose what the
CLI can't accept.

**Recommended `Area` shape** (for the story to realize; spelling is the dev's to
finalize): `center (lat, lon)` + `half_width_km` + `half_height_km` + `angle_deg`
(bearing of the box's long axis, 0 = axis-aligned). `--radius` stays as the
centered-square shorthand (`half_width = half_height = radius`, `angle = 0`) for
backward compatibility and the byte-identical golden guarantee.

---

## Section 4 — Detailed Change Proposals

> Applied on approval. Grouped by artifact.

### 4.1 — `prd.md`

**FR1** — OLD:
> - FR1: User can specify a search area via center point and radius.

NEW:
> - FR1: User can specify a search area as a rectangle — a center point plus width
>   and height — optionally rotated by a bearing angle so the box can align to a
>   diagonally-oriented feature (e.g. a mountain range). A single radius remains a
>   shorthand for a centered, axis-aligned square (the `angle = 0`, equal-extents
>   case). Arbitrary (free-form) polygons are out of v1 scope.

**FR2** — OLD:
> - FR2: System rejects search areas exceeding the configured area-size cap with a descriptive error.

NEW:
> - FR2: System rejects search areas exceeding the configured area-size cap with a
>   descriptive error, using the rectangle's true area (`width × height`), not a
>   radius-derived proxy.

**FR10** — OLD:
> - FR10: System searches for routes maximizing total vertical effort (D+ + D−) subject to the configured constraints, with returned routes strictly contained within the specified search area.

NEW (append clause):
> …with returned routes strictly contained within the specified search area
> (containment tested against the rotated-rectangle polygon).

### 4.2 — `epics.md` (CLI track)

Add to the **Epic List** table (Active/future section) and append the full epic
detail:

```
## Epic 15: Rotated-Rectangle Search Areas

Generalizes the search area from a centered square to a rotated rectangle so it
can hug a diagonally-oriented range (Belledonne SW–NE) and keep off-axis valley
out of the expensive setup phase. Axis-aligned rectangle and square are the
angle=0 / equal-extents cases of one unified model — no separate rectangle
increment. The solver, validator, climb detection, and contraction are geometry-
blind and untouched; the change lives in the Area model, setup fetch, cache
key/schema, coverage, CLI flags, validation, and the render overlay. Backward-
compat guardrail: existing --center/--radius runs stay byte-identical (no golden
rebake); the rotated shape gets its own regression coverage. Inserted via
correct-course 2026-07-24; no epic renumber.

**FRs covered:** FR1 (generalized), FR2 (true-area cap), FR10 (rotated containment).

### Story 15.1: Generalize the Area model and geometry helpers

As a developer, I want the Area type and its polygon/bbox helpers to represent a
rotated rectangle (square/axis-aligned rectangle as special cases), so that all
downstream geometry derives from one model.

**Acceptance Criteria:**
- **Given** an Area with center + half-extents + rotation angle, **when** its
  polygon is derived, **then** corners are computed in a local cos(lat) km frame,
  rotated, and converted back to WGS84; angle=0 with equal extents reproduces
  today's square ring exactly.
- **Given** any Area, **when** the axis-aligned-envelope helper is called,
  **then** it returns the true min/max of the (possibly rotated) polygon and is
  clearly named/documented as an *envelope*, not "the region".
- **Given** the square shorthand (radius), **when** an Area is built, **then** it
  maps to equal half-extents at angle 0 and is indistinguishable from a v1 Area
  downstream.
- **Given** the geometry helpers, **when** unit-tested, **then** rotation is
  verified against known corner coordinates and the degree-space-skew case is
  covered.

### Story 15.2: Rotated-aware setup fetch, cache schema, and coverage

As a user, I want setup to fetch and cache exactly the rotated rectangle and
queries to resolve coverage against it, so that off-axis valley is never
pre-processed and cached areas are keyed correctly.

**Acceptance Criteria:**
- **Given** a rotated Area, **when** setup fetches OSM, **then** it uses
  `graph_from_polygon` over the rotated ring (reusing osmnx truncation), and the
  cached graph contains only edges within the rotated rectangle.
- **Given** the cache key, **when** an Area is canonicalized, **then** a new area
  mode encodes center + half-extents + angle (rounded), and two areas differing
  only in angle or extent produce different keys.
- **Given** the manifest/index schema, **when** the new fields are added, **then**
  the schema version is bumped and pre-existing entries re-prepare once (existing
  invalidation semantics; no compat shim).
- **Given** a query area, **when** coverage is checked, **then** containment tests
  the rotated polygon via `shapely.contains`, the partial-coverage/"try a bigger
  area" messaging is corrected off the scalar-radius assumption, and every
  bbox-envelope shortcut (`area_bbox_wgs84` et al.) is audited so coverage does
  not over-report.
- **Given** an existing square entry prepared post-migration, **when** queried,
  **then** results are unchanged from v1.

### Story 15.3: CLI flag surface, validation, and render overlay

As a user, I want CLI flags to specify a rotated rectangle (with radius still
meaning a square) and the report overlay to draw the true box, so that the
capability is usable and honestly visualized.

**Acceptance Criteria:**
- **Given** the setup and query CLIs, **when** area flags are parsed, **then** a
  rotated rectangle can be specified (width/height/angle) and `--radius` still
  produces a centered square; both CLIs accept the identical surface (FR23).
- **Given** the area-cap check, **when** it validates, **then** it uses the true
  rectangle area (`width × height`), rejecting oversize boxes with a descriptive
  message (BadCLIArgError / exit 2).
- **Given** a rendered report, **when** the search-area overlay draws, **then** it
  draws the rotated rectangle, not an axis-aligned proxy.
- **Given** the regression suite, **when** it runs, **then** existing square
  goldens pass untouched and at least one rotated-rectangle golden is added.
- **Given** the docs, **when** updated, **then** the quality-demo params note and
  README area examples reflect the new surface.
```

Add to the **Active/future** table header row set and the **FR Coverage Map**:

```
| FR1 (generalized: rotated rect) | Epic 1 (square) / Epic 15 (rotated) | Area model + osmnx graph_from_polygon |
| FR2 (true-area cap)             | Epic 1 (initial) / Epic 15 (true area) | rectangle area, not disk proxy |
| FR10 (rotated containment)      | Epic 3 / Epic 15 | shapely.contains on rotated polygon |
```

### 4.3 — `architecture.md` (CLI)

- **Cat 3:** note setup fetch uses `graph_from_polygon(rotated ring)` for
  non-square areas (reuses `truncate_graph_polygon`); square path unchanged.
- **Cat 4b:** document the new area **mode** in `_canonicalize_area` (center +
  half-extents + angle, rounded) alongside `center_radius`.
- **Cat 4c + manifest schema:** bump `schema_version`; the `area` block gains
  extents + angle; existing entries re-prepare once.
- **Cat 4e:** record that containment uses the rotated polygon and that
  bbox-envelope helpers are envelopes (over-approximations), not the region.
- **Cat 5:** add a one-line note that box shape does not reach the solver.

### 4.4 — App track

**`epics-app.md`** — add:

```
FR15: Rotated-rectangle map selection — the map picker can define and edit a
rotated rectangle (center + two dimensions + rotation), pass it to setup/query
argv, and render built rotated regions as their true polygons. → App Epic 5.
```

```
## Epic 5: Rotated-Rectangle Map Selection

Exposes CLI Epic 15's rotated-rectangle areas in the map picker. Adds a second
dimension handle and a rotation handle to the area-pick mode, generalizes
move-selection and select-region to rotated geometry, plumbs the new shape
through AreaSpec → argv, and renders built regions as their true (possibly
rotated) polygons. Depends on CLI Epic 15. Additive on App Epics 1–4; no
rollback. Inserted via correct-course 2026-07-24.

**FRs covered:** FR15.

### Story 5.1: Rotated AreaSpec, argv, and regions plumbing
- **Given** a rotated area from the picker, **when** a job is created, **then**
  AreaSpec carries center + dimensions + angle and argv builds the CLI Epic 15
  flags (square still emits --radius for backward compat).
- **Given** GET /regions, **when** a built rotated region is returned, **then**
  it carries its true polygon (RegionBounds generalized), and any axis-aligned
  envelope it also exposes is documented as such (App-side envelope-leak audit).
- **Given** the argv/regions seams, **when** tested, **then** rotated and square
  round-trip correctly through cli_adapter.

### Story 5.2: Map picker rotation and dimension handles
- **Given** area-pick mode, **when** I edit the selection, **then** I can set two
  dimensions and a rotation angle; the box renders as an L.polygon; a square is
  still expressible.
- **Given** move-selection mode, **when** I drag, **then** the rotated box
  translates rigidly and coverage re-resolves on release.
- **Given** select-region mode, **when** I click a built rotated region, **then**
  the selection snaps to its exact rotated geometry and Configure query enables.
```

Add FR15 to the App **FR Coverage Map** (→ Epic 5) and revise **UX-DR1** with a
post-v1 note: area-pick gains dimension + rotation handles; overlays render true
polygons.

**`architecture-app.md`** — note Cat 6 (`RegionBounds` → polygon) and Cat 9
(argv seam gains rotated flags; `params_schema` still excludes area fields).

**`ux-design-specification.md`** — S1 map-home note: rotation + dimension handles.

### 4.5 — `future-ideas.md`

Append:
```
**Promoted 2026-07-24:** rotated-rectangle search areas pulled into CLI Epic 15
+ App Epic 5 via correct-course
(`sprint-change-proposal-2026-07-24-rotated-rectangle-areas.md`). Arbitrary
polygons remain out of scope (map-drawing cost not justified).
```

### 4.6 — `sprint-status.yaml`

Update `last_updated`; append:
```yaml
  epic-15: backlog        # Rotated-Rectangle Search Areas (correct-course 2026-07-24)
  15-1-generalize-area-model-and-geometry-helpers: backlog
  15-2-rotated-setup-fetch-cache-schema-and-coverage: backlog
  15-3-cli-flag-surface-validation-and-render-overlay: backlog

  app-epic-5: backlog     # Rotated-Rectangle Map Selection (correct-course 2026-07-24)
  app-5-1-rotated-areaspec-argv-and-regions-plumbing: backlog
  app-5-2-map-picker-rotation-and-dimension-handles: backlog
```

---

## Section 5 — Implementation Handoff

- **Scope classification:** **Moderate** — net-new epics on two tracks, backlog
  reorganization, cross-artifact edits. Not Major (no PRD goal/MVP replan; no
  architecture-pattern change) and not Minor (more than a single-story direct
  implementation).
- **Recipients & responsibilities:**
  - **PO/DEV (this proposal):** apply the artifact edits in Section 4 and the
    `sprint-status.yaml` entries on approval.
  - **create-story → dev-story (Amelia):** realize Epic 15 stories 15.1→15.3 in
    order, then App Epic 5 stories 5.1→5.2. CLI epic first (App depends on it).
- **Success criteria:**
  1. A rotated rectangle can be specified via CLI and prepared/queried end-to-end.
  2. Existing `--center/--radius` runs are byte-identical; regression goldens pass
     with **no rebake**; a rotated-rectangle golden is added.
  3. Coverage, cache keying, and the report overlay reflect the true rotated box
     (envelope-leak audit complete on both CLI and App).
  4. The App map picker can define, move, and select-region a rotated rectangle.

---

## Approval

- [x] **Approved for implementation** (Yann, 2026-07-24) — all Section 4 edits and
  the `sprint-status.yaml` entries applied; handoff to create-story for Epic 15.
- [ ] Revise (feedback below)
```
