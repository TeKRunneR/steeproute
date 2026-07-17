# Sprint Change Proposal — App UX improvements

**Date:** 2026-07-17
**Author:** Yann (via correct-course)
**Track:** web App (`epics-app.md`)
**Trigger:** `future-ideas.md` → "App UX improvements" section
**Scope classification:** Moderate (additive epic + backlog reorganization; PO/DEV handoff)

---

## Section 1 — Issue Summary

The App track (App Epics 1–3) shipped complete and `done`. Hands-on use of the
finished app surfaced four concrete UX friction points, captured in
`future-ideas.md` → "App UX improvements". These are not defects in shipped
stories (each met its ACs) — they are refinements that only become visible once
the whole loop (pick → build → query → browse) is used repeatedly:

1. **Map has only one interaction mode.** Clicking always drops a *new* center
   ([map-home.js:127](../../src/steeproute/app/static/js/map-home.js)). There is
   no way to (a) click an existing built (green) region to query it as-is, or
   (b) reposition the whole selection box without re-clicking a fresh point.
   Querying an already-built region means fiddling to reproduce its exact
   center+radius so the selection reads as "covered".
2. **The config form's basic/advanced split is dead weight.** Nearly every query
   parameter matters for this tool, so hiding most of them behind a collapsed
   "Advanced" `<details>` ([config-form.js:94](../../src/steeproute/app/static/js/config-form.js))
   just adds a click. The split (`_BASIC_FIELDS` in
   [params_schema.py:66](../../src/steeproute/app/cli_adapter/params_schema.py))
   earns nothing.
3. **Runs are unidentifiable.** Run cards show raw `center lat, lon`
   ([runs.js:34](../../src/steeproute/app/static/js/runs.js)) — GPS coordinates
   are not human-memorable, so it is nearly impossible to recall *which run was
   where*. Additionally, a query's stored `params` are never shown anywhere in
   the UI, so a past run's configuration is invisible.
4. **Long numbers are hard to parse; two defaults are wrong for real use.**
   `1000000` (iter budget) is unreadable without grouping; native number inputs
   show no thousands separator. Commas are unusable (French decimal separator).
   Separately: the tool's own point is steep routes, so `--max-descent-slope`
   should default on (0.4) and `--start-at-junction` should default checked —
   but the App inherits the CLI's off/false defaults.

**Evidence:** direct code inspection of the shipped App (paths above) confirms
each gap; the requests are recorded verbatim in `future-ideas.md`.

---

## Section 2 — Impact Analysis

### Epic impact
- **App Epics 1–3:** unaffected — all `done`; no rollback, no re-open. These are
  additive refinements layered on their delivered surfaces.
- **New App Epic 4 — App UX refinements:** the natural home for all four items,
  as **three** stories — the config-form pane changes (flat layout + readable
  numbers + corrected defaults) are merged into one story since they touch the
  same surface. No cross-story ordering dependency; any order works.

### Artifact impact
- **`epics-app.md`** — add FR11–FR14, UX-DR updates, and Epic 4 + four stories.
- **`ux-design-specification.md`** — S1 gains explicit selection modes (§F1/F2
  flows updated), S2 loses the basic/advanced split, S4 gains area-label + a
  query-params expander. This also resolves the Cluster-D "run-card fields" open
  question left dangling in the UX spec §4/§5.
- **`architecture-app.md`** — one *new* outbound seam: reverse-geocoding
  (Nominatim) for the town label. This is **not** a `cli_adapter` change — that
  boundary is CLI-coupling only ([architecture-app.md:342](../../_bmad-output/planning-artifacts/architecture-app.md)).
  A small `app/geocode.py` module owns it; JobRecord gains `area_label`.
- **`future-ideas.md`** — mark the "App UX improvements" section promoted.
- **`sprint-status.yaml`** — add `app-epic-4` + four story keys as `backlog`.

### Technical impact (per story)
- **4.1 (map modes):** `map-home.js` — a mode toggle; make region overlays
  clickable in select-region mode; drag-the-box in move mode; keep click-to-place
  as the default area-pick mode. `select-region` snaps the selection to the built
  region's exact geometry (already available via `GET /regions`) and enables
  "Configure query" directly. No backend change (geometry already server-authored).
- **4.2 (config-form overhaul — merged):** remove the `<details>` wrapper in
  `config-form.js` and drop (or neutralize) `_BASIC_FIELDS`/`group` in
  `params_schema.py` so all flags render at once; switch long-number fields to
  space-grouped text inputs (format on blur, parse to plain int on submit); add
  `max_descent_slope: 0.4` and `start_at_junction: True` to `_QUALITY_DEFAULTS`.
  A small shared number-format helper is factored out so 4.3's run-library param
  view groups the same way.
- **4.3 (recognizable runs):** new `app/geocode.py` (best-effort Nominatim reverse
  geocode, offline-safe → `None` on failure); `JobRecord.area_label: str | None`
  stamped at job creation ([api.py:90](../../src/steeproute/app/api.py)); `runs.js`
  renders the label and a collapsible query-params view (data already in
  `job.params`), reusing 4.2's format helper for grouped numbers.

### NFR check
Consistent with the App's settled posture (NFR3 single-user/local/no-auth; NFR5
thinness). The one new external dependency (Nominatim) mirrors the existing
outbound tile fetch (OpenTopoMap) — same connectivity assumption, best-effort.

---

## Section 3 — Recommended Approach

**Direct Adjustment — add App Epic 4 (four stories).** No rollback (nothing
shipped is wrong), no MVP/PRD change (the App has no PRD; the CLI PRD is
untouched). Additive, low-risk, each story independently shippable.

- **Effort:** Low–Medium overall. 4.2 is contained (one pane); 4.1 and 4.3 carry
  the real work (map interaction modes; a new geocode seam + record field).
- **Risk:** Low. Only 4.3 adds a dependency (Nominatim), scoped best-effort so a
  failed/absent lookup degrades to today's coordinate display, never blocks a job.
- **Timeline:** post-v1 increment; fits the App track's cadence. No dependency on
  the parked CLI-side Epic 14.6 r50 probe.

**Settled design decisions (from correct-course Q&A):**
- Item 3 area identity → **town name via reverse geocode** (not thumbnail).
- Item 4 numbers → **text input, space-grouped** (`1 000 000`), grouping in form
  and run-library display.

---

## Section 4 — Detailed Change Proposals

### 4a. `epics-app.md` — Requirements Inventory additions

**ADD to Functional Requirements:**

```
FR11: Map selection modes — the map picker offers explicit, switchable modes:
(a) area-pick (default; click drops a new center, drag handle sets radius —
today's behavior), (b) move-selection (drag the whole selection box to
reposition it), (c) select-region (click a built/green overlay to snap the
selection to that region's exact geometry and enable "Configure query"
directly). Modes are separate so area-pick never has to double as region-select.

FR12: Flat config form — the query config form exposes ALL parameters at once
with no basic/advanced collapse; every flag is always visible.

FR13: Recognizable runs — each run carries a human area label (a nearby town/
place name reverse-geocoded from the center, stored on the job record), shown on
its run-library card for both setup and query jobs; a query card additionally
exposes its full stored parameter set on demand (click to reveal).

FR14: Readable numbers — long numeric values (e.g. iter budget) render with
space thousands separators (never commas — French decimal collision) in both the
config form inputs and the run-library parameter display.
```

**ADD to UX Design Requirements (revisions):**

```
UX-DR1 (revised): S1 Map home gains three explicit selection modes (FR11);
area-pick stays the default and behaves exactly as shipped. Region overlays are
inert except in select-region mode.

UX-DR2 (revised): S2 Config form drops the collapsible advanced section — all
flags render in one always-visible list (FR12). Quality-demo defaults now
include max_descent_slope=0.4 and start_at_junction=on.

UX-DR4 (revised): S4 run cards lead with the town label (FR13) instead of raw
coordinates as the primary identifier; coordinates remain as secondary detail.
Query cards add a click-to-reveal parameter view. This resolves the Cluster-D
"run-card fields" open question the UX spec left deferred.
```

**ADD to FR Coverage Map:** `FR11–FR14 → Epic 4`.

### 4b. `epics-app.md` — new Epic 4 section

```
### Epic 4: App UX refinements
Post-v1 refinements from hands-on use of the finished app: switchable map
selection modes (incl. click-to-query a built region), a flat all-flags config
form, human-recognizable runs (town label + a query-params view), and readable
space-grouped numbers with corrected steep-route defaults. Additive on the
delivered App Epics 1–3; no rollback.
**FRs covered:** FR11, FR12, FR13, FR14

#### Story 4.1: Map selection modes (area-pick / move / select-region)
As a user, I want distinct map modes so I can drop a new area, reposition the
whole selection, or click a built region to query it directly — without one mode
having to do double duty.
Outcomes: a visible mode control; area-pick is the default and unchanged; move
mode drags the whole box; select-region makes green overlays clickable and snaps
the selection to the built region's exact geometry, enabling "Configure query"
directly. No backend change (geometry from GET /regions).

#### Story 4.2: Config form overhaul — flat layout, readable numbers, corrected defaults
As a user, I want every query parameter visible at once (they all matter), long
numbers grouped for readability, and defaults that fit a steep-route tool.
Outcomes: the advanced <details> is removed and all schema fields render in one
always-visible list; the basic/advanced grouping is retired from params_schema
without breaking argv/validation (schema stays the single source of truth);
long-number fields use space-grouped text inputs (format on blur, parse to plain
int on submit — never commas; grouping is display-only, the wire/argv values stay
plain); quality defaults add max_descent_slope=0.4 and start_at_junction on. A
small number-format helper is factored for reuse by Story 4.3.

#### Story 4.3: Recognizable runs (town label + query-params view)
As a user, I want to recognize past runs by place and inspect a query's config.
Outcomes: a best-effort reverse-geocode (own module, offline-safe) resolves the
center to a town label stored on the job record at creation; run cards (setup and
query) show it as the primary identifier; query cards expose the full stored
params on demand, with long numbers grouped via 4.2's format helper. A failed/
absent lookup degrades to today's coordinate display.
```

### 4c. `ux-design-specification.md`
Apply the UX-DR revisions above: update the S1 flow notes (F1/F2) for the three
modes, drop the basic/advanced language from S2, and update the S4 wireframe
run-card description to lead with the town label + params expander. Remove the
"run-card fields" item from the §5 deferred Cluster-D list (now decided).

### 4d. `future-ideas.md`
Append a promotion note under the "App UX improvements" heading:
`**Promoted 2026-07-17:** these four items pulled into App Epic 4 via
correct-course (sprint-change-proposal-2026-07-17-app-ux-improvements.md).`

### 4e. `sprint-status.yaml`
Add under the App track:
```
app-epic-4: backlog        # App UX refinements (correct-course 2026-07-17)
app-4-1-map-selection-modes: backlog
app-4-2-config-form-overhaul-flat-numbers-defaults: backlog
app-4-3-recognizable-runs-town-label-and-params-view: backlog
```

---

## Section 5 — Implementation Handoff

- **Scope:** Moderate — a new epic + four backlog stories + planning-artifact
  updates. No architectural re-plan; one small new module (`app/geocode.py`) and
  one new record field noted in `architecture-app.md`.
- **Route to:** PO/DEV. On approval: apply the planning-artifact edits (4a–4e),
  then implement story-by-story via `bmad-create-story` → `bmad-dev-story`.
- **Suggested order:** 4.2 (contained quick win) → 4.1 → 4.3. Any order is valid.
- **Success criteria:** each story's outcomes met; the four `future-ideas.md`
  frictions resolved; App Epics 1–3 behavior preserved (area-pick default
  unchanged; geocode failure degrades gracefully).
```
