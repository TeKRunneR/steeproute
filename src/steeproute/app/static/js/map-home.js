// S1 Map home — pick a (possibly rotated) rectangle, see cached regions, build an
// uncached one (architecture-app.md §Category 6/10; UX S1/F2). Vanilla ES module,
// Leaflet from the vendored CLI copy, all backend calls through api.js (the only
// URL holder).
//
// The server is the single authority for geometry: the selection's TRUE polygon
// and the green/grey coverage decision come from `GET /regions/resolve`, computed
// by the CLI cache's own km→deg conversion and orientation-aware containment.
// This file re-derives NEITHER — it passes the picked shape through and draws the
// polygon the server returns, so the overlay can't drift from query-side coverage.
// The axis-aligned `bounds` envelope the responses also carry is deliberately
// never read: it is strictly larger than a rotated box (Story app-5-2 closes that
// envelope leak).
//
// The only geometry computed here turns a dragged handle into an INPUT scalar: a
// distance from Leaflet's own `map.distance` (library geodesy) and a bearing in
// the same local cos(lat) km frame the CLI rotates the box in.

import { createJob, getJob, listRegions, resolveArea, runWatchUrl } from "./api.js";
import { openConfigForm } from "./config-form.js";
import { areaGeometry } from "./format.js";

const DEFAULT_RADIUS_KM = 10;
const MIN_HALF_KM = 0.5; // floor on a half-extent, as the radius handle has always had
// How far outside the height-axis edge the rotation grip sits. Kept close to 1
// (code review, app-5-2) so the grip stays reachable on a large or
// height-dominant box — exactly the diagonal-range case this epic targets —
// rather than drifting off-screen at typical zoom.
const ROTATE_HANDLE_OFFSET = 1.1;
const GRENOBLE = [45.19, 5.72];

// --- DOM ---------------------------------------------------------------------
const readoutEl = document.getElementById("selection-readout");
const centerEl = document.getElementById("sel-center");
const shapeEl = document.getElementById("sel-shape");
const coverageEl = document.getElementById("sel-coverage");
const resetBtn = document.getElementById("reset-square-btn");
const buildBtn = document.getElementById("build-btn");
const configureBtn = document.getElementById("configure-btn");
const statusEl = document.getElementById("picker-status");
const hintEl = document.getElementById("picker-hint");
const modeControlEl = document.getElementById("mode-control");

// Selection modes (Story 4.1 / FR11). Exclusive: the map click only drops a
// center in area-pick; only in move-selection is the whole box draggable; only
// in select-region are the green overlays clickable. Per-mode hint copy too.
const MODE_HINTS = {
  "area-pick": "Click the map to drop a center, then drag ↔ to size it, ↕ for a second dimension, and ⟳ to rotate.",
  "move-selection": "Drag the selection to reposition it — its size and angle stay the same.",
  "select-region": "Click a green built region to select it for querying.",
};

const COVERED_STYLE = { color: "#3a923f", weight: 2, fillOpacity: 0.05 };
const UNCOVERED_STYLE = { color: "#8a94a6", weight: 2, dashArray: "6 4", fillOpacity: 0.05 };

// --- Map ---------------------------------------------------------------------
const map = L.map("map").setView(GRENOBLE, 11);
// OSM-derived OpenTopoMap basemap — topographic, key-free and referer-tolerant,
// same tiles the CLI HTML report uses (map tiles are a tile-server fetch, not a
// vendored JS/CSS asset).
L.tileLayer("https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", {
  maxZoom: 17,
  subdomains: "abc",
  attribution:
    "Map data &copy; OpenStreetMap contributors, SRTM | Style: &copy; OpenTopoMap (CC-BY-SA)",
}).addTo(map);
// The map lives in a flexbox that finishes sizing after first paint; recompute
// Leaflet's cached pixel dimensions so click→latlng maps to the true point
// (otherwise a stale size offsets the dropped center).
requestAnimationFrame(() => map.invalidateSize());

// --- State -------------------------------------------------------------------
let center = null; // {lat, lon}
// The picked shape, and the single source of truth for what goes on the wire —
// NOT re-derived from the resolve response, so the spelling can't flip-flop under
// the user. Two forms, mirroring the CLI flag surface (exactly one is ever sent):
//   {kind: "radius", radiusKm}                    → `radius_km` (centered square)
//   {kind: "box", widthKm, heightKm, angleDeg}    → `width_km`/`height_km`/`angle_deg`
// A fresh drop is the square spelling, so the shipped square path (argv, cache
// key, coverage) is bit-for-bit what it was before this story.
let shape = { kind: "radius", radiusKm: DEFAULT_RADIUS_KM };
let selectionPoly = null;
let widthHandle = null;
let heightHandle = null;
let rotateHandle = null;
let centerMarker = null; // move-selection drag handle; present only in that mode
let mode = "area-pick";
let resolveSeq = 0; // drop out-of-order resolve responses

// Draggable HTML handles — a divIcon needs no marker-image asset (the vendored
// Leaflet ships JS/CSS only, like the CLI report which uses no markers).
const widthIcon = L.divIcon({ className: "map-handle", iconSize: [16, 16] });
const heightIcon = L.divIcon({ className: "map-handle map-handle-height", iconSize: [16, 16] });
const rotateIcon = L.divIcon({ className: "map-rotate-handle", iconSize: [18, 18] });
const moveIcon = L.divIcon({ className: "map-move-handle", iconSize: [18, 18] });

// --- Shape helpers -----------------------------------------------------------

/** The wire `area` body for the current selection — exactly ONE spelling, since
 *  the server rejects a body carrying both radius and dimensions (422). */
function shapeToArea() {
  const at = [center.lat, center.lon];
  if (shape.kind === "radius") return { center: at, radius_km: shape.radiusKm };
  return {
    center: at,
    width_km: shape.widthKm,
    height_km: shape.heightKm,
    angle_deg: shape.angleDeg,
  };
}

/** Effective FULL `(width, height)` km of the current shape whichever spelling it
 *  uses — `radius_km` is a half-side, so a square's full dimensions are `2r`. */
function fullDimensions() {
  return shape.kind === "radius"
    ? [2 * shape.radiusKm, 2 * shape.radiusKm]
    : [shape.widthKm, shape.heightKm];
}

function currentAngle() {
  return shape.kind === "radius" ? 0 : shape.angleDeg;
}

/** The same geometry in the explicit box spelling — what a second dimension or a
 *  bearing promotes the square shorthand to. */
function asBox() {
  const [widthKm, heightKm] = fullDimensions();
  return { kind: "box", widthKm, heightKm, angleDeg: currentAngle() };
}

/** Half-extent in km from the center out to a dragged handle, measured by
 *  Leaflet's own geodesy (metres).
 *
 *  A handle sits at an edge MIDPOINT, i.e. exactly one half-extent from the
 *  center — while the wire takes FULL dimensions, so every caller doubles this.
 *  Mixing the two up is the silent-halving trap (a box quietly half the size the
 *  user drew, with no error anywhere). */
function handleHalfKm(handle) {
  const km = map.distance(L.latLng(center.lat, center.lon), handle.getLatLng()) / 1000;
  return Math.max(MIN_HALF_KM, km);
}

/** Bearing of `pos` seen from the center, in degrees clockwise from north.
 *
 *  Computed in the local cos(lat) km frame the CLI rotates the box in
 *  (`cache._area_to_polygon`). Reading the angle off screen pixels or raw degree
 *  deltas instead would skew it — a degree of longitude is shorter on the ground
 *  than a degree of latitude — and the drawn box would visibly lag the cursor.
 *  Folded into [0, 180) since a 180° bearing describes the very same rectangle. */
function bearingDeg(pos) {
  const north = pos.lat - center.lat;
  const east = (pos.lng - center.lon) * Math.cos((center.lat * Math.PI) / 180);
  if (north === 0 && east === 0) return currentAngle();
  const deg = (Math.atan2(east, north) * 180) / Math.PI;
  return ((deg % 180) + 180) % 180;
}

// --- Rendering ---------------------------------------------------------------

function midpoint(a, b) {
  return [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
}

/** Push a point away from the center by `factor` — handle placement only, never
 *  the selected area. */
function outward(point, factor) {
  return [
    center.lat + (point[0] - center.lat) * factor,
    center.lon + (point[1] - center.lon) * factor,
  ];
}

/** Snap the area-pick handles onto the authoritative polygon: the width handle to
 *  the +width edge midpoint, the height handle to the +height edge midpoint, and
 *  the rotation grip just outside the latter.
 *
 *  Vertices arrive in box-local ring order — SW, SE, NE, NW
 *  (`cli_adapter.regions._polygon_latlon`) — so SE→NE spans the +width edge and
 *  NE→NW the +height edge, and these are plain midpoints of server-authored
 *  points. No geometry is re-derived: rotation is already baked into the ring. */
function positionHandles(polygon) {
  if (!widthHandle || polygon.length < 4) return;
  const [, se, ne, nw] = polygon;
  const heightMid = midpoint(ne, nw);
  widthHandle.setLatLng(midpoint(se, ne));
  heightHandle.setLatLng(heightMid);
  rotateHandle.setLatLng(outward(heightMid, ROTATE_HANDLE_OFFSET));
}

// Render the server-resolved selection: its true polygon, coverage-driven
// styling, the readout, and the action-button states. `snapHandles` pulls the
// handles back onto the (authoritative) edges; skipped mid-drag so they don't
// fight the user's cursor.
function applyResolution(res, { snapHandles }) {
  const covered = res.covered;

  if (selectionPoly) selectionPoly.remove();
  selectionPoly = L.polygon(res.polygon, covered ? COVERED_STYLE : UNCOVERED_STYLE).addTo(map);

  if (snapHandles) positionHandles(res.polygon);
  // Keep the move handle (when present) snapped to the authoritative center.
  if (centerMarker) centerMarker.setLatLng([center.lat, center.lon]);

  readoutEl.hidden = false;
  centerEl.textContent = `${center.lat.toFixed(4)}, ${center.lon.toFixed(4)}`;
  // Shared with the run library / run watch / result view, so the App names a box
  // the same way everywhere (and the same way the CLI does).
  shapeEl.textContent = areaGeometry(res);
  coverageEl.textContent = covered ? "cached — ready to query" : "needs build";
  resetBtn.hidden = shape.kind === "radius";

  buildBtn.disabled = covered;
  configureBtn.disabled = !covered;
  configureBtn.title = covered ? "" : "Build this region first";
  statusEl.textContent = "";
}

async function resolveAndRender({ snapHandles }) {
  const seq = ++resolveSeq;
  try {
    const res =
      shape.kind === "radius"
        ? await resolveArea(center.lat, center.lon, shape.radiusKm)
        : await resolveArea(center.lat, center.lon, null, {
            widthKm: shape.widthKm,
            heightKm: shape.heightKm,
            angleDeg: shape.angleDeg,
          });
    if (seq === resolveSeq) applyResolution(res, { snapHandles }); // ignore stale
  } catch (err) {
    if (seq === resolveSeq) statusEl.textContent = `Could not resolve area: ${err.message ?? err}`;
  }
}

// --- Handles -----------------------------------------------------------------

function makeHandle(icon, onDragEnd) {
  const marker = L.marker([center.lat, center.lon], { icon, draggable: true }).addTo(map);
  // Resolve on release so the polygon and coverage snap to the server's canonical
  // geometry; Leaflet moves the handle freely during the drag.
  marker.on("dragend", onDragEnd);
  return marker;
}

function ensureHandles() {
  if (widthHandle) return;

  // Width: on a square this resizes the square and KEEPS the radius spelling —
  // exactly the shipped radius-handle behaviour. Only a second dimension or a
  // bearing promotes the selection to the box spelling.
  widthHandle = makeHandle(widthIcon, () => {
    const halfKm = handleHalfKm(widthHandle);
    shape =
      shape.kind === "radius"
        ? { kind: "radius", radiusKm: halfKm }
        : { ...shape, widthKm: 2 * halfKm };
    void resolveAndRender({ snapHandles: true });
  });

  heightHandle = makeHandle(heightIcon, () => {
    const halfKm = handleHalfKm(heightHandle);
    shape = { ...asBox(), heightKm: 2 * halfKm };
    void resolveAndRender({ snapHandles: true });
  });

  rotateHandle = makeHandle(rotateIcon, () => {
    shape = { ...asBox(), angleDeg: bearingDeg(rotateHandle.getLatLng()) };
    void resolveAndRender({ snapHandles: true });
  });
}

function handleList() {
  return [widthHandle, heightHandle, rotateHandle].filter(Boolean);
}

// The move-selection drag handle: a draggable center marker that translates the
// whole selection. During the drag the existing (server-authored) polygon and the
// handles are shifted rigidly by the lat/lon delta — a pure translation, no km→deg
// derived in JS, shape and bearing untouched — then on release the polygon and
// coverage re-resolve from the server so the canonical geometry replaces it.
function ensureCenterMarker() {
  if (centerMarker || !center) return;
  centerMarker = L.marker([center.lat, center.lon], {
    icon: moveIcon,
    draggable: true,
  }).addTo(map);

  let start = null; // {lat, lon, ring, handles} captured at dragstart
  centerMarker.on("dragstart", () => {
    start = selectionPoly
      ? {
          lat: center.lat,
          lon: center.lon,
          ring: selectionPoly.getLatLngs()[0].map((p) => [p.lat, p.lng]),
          handles: handleList().map((h) => [h.getLatLng().lat, h.getLatLng().lng]),
        }
      : null;
  });
  centerMarker.on("drag", () => {
    if (!start) return;
    const p = centerMarker.getLatLng();
    const dLat = p.lat - start.lat;
    const dLon = p.lng - start.lon;
    selectionPoly.setLatLngs(start.ring.map(([lat, lon]) => [lat + dLat, lon + dLon]));
    handleList().forEach((h, i) => {
      const [lat, lon] = start.handles[i];
      h.setLatLng([lat + dLat, lon + dLon]);
    });
  });
  centerMarker.on("dragend", () => {
    const p = centerMarker.getLatLng();
    center = { lat: p.lat, lon: p.lng };
    void resolveAndRender({ snapHandles: true }); // shape unchanged
  });
}

function removeCenterMarker() {
  if (!centerMarker) return;
  centerMarker.remove();
  centerMarker = null;
}

// Apply the interaction rules for the active mode. Exclusive by construction:
// the size/rotation handles drag only in area-pick; the move handle exists only
// in move-selection; region overlays get the pointer cursor only in select-region.
function applyModeInteractivity() {
  for (const handle of handleList()) {
    if (mode === "area-pick") handle.dragging.enable();
    else handle.dragging.disable();
  }
  if (mode === "move-selection") ensureCenterMarker();
  else removeCenterMarker();
  map.getContainer().classList.toggle("select-region-active", mode === "select-region");
  hintEl.textContent = MODE_HINTS[mode];
}

// --- Regions -----------------------------------------------------------------

function drawRegions(regions) {
  for (const r of regions) {
    // The region's TRUE (possibly rotated) polygon — not its axis-aligned
    // envelope, which would draw a rotated region too large.
    const overlay = L.polygon(r.polygon, {
      className: "region-overlay",
      color: "#3a923f",
      weight: 2,
      fillOpacity: 0.12,
    }).addTo(map);
    // select-region: snap the selection to this built region's exact geometry
    // (server-authored, in the region's own spelling) and let coverage re-resolve
    // → "Configure query" enabled. Inert in the other modes (the guard returns).
    overlay.on("click", (ev) => {
      if (mode !== "select-region") return;
      L.DomEvent.stopPropagation(ev); // don't also fall through to the map click
      center = { lat: r.center[0], lon: r.center[1] };
      shape =
        r.radius_km != null
          ? { kind: "radius", radiusKm: r.radius_km }
          : { kind: "box", widthKm: r.width_km, heightKm: r.height_km, angleDeg: r.angle_deg };
      ensureHandles();
      applyModeInteractivity(); // keep the freshly-created handles non-draggable here
      void resolveAndRender({ snapHandles: true });
    });
  }
}

// --- Interactions ------------------------------------------------------------

map.on("click", (ev) => {
  if (mode !== "area-pick") return; // only area-pick drops a new center
  center = { lat: ev.latlng.lat, lon: ev.latlng.lng };
  shape = { kind: "radius", radiusKm: DEFAULT_RADIUS_KM }; // a fresh drop is a square
  ensureHandles();
  applyModeInteractivity(); // handles are draggable in area-pick; no move marker
  void resolveAndRender({ snapHandles: true });
});

modeControlEl.addEventListener("change", (ev) => {
  if (ev.target.name !== "map-mode") return;
  mode = ev.target.value;
  applyModeInteractivity();
});

// Back to the centered-square spelling, keeping the width as the square's side
// (predictable: the dimension the user sized first is the one that survives).
resetBtn.addEventListener("click", () => {
  if (!center || shape.kind === "radius") return;
  shape = { kind: "radius", radiusKm: Math.max(MIN_HALF_KM, shape.widthKm / 2) };
  void resolveAndRender({ snapHandles: true });
});

buildBtn.addEventListener("click", async () => {
  if (!center) return;
  buildBtn.disabled = true;
  statusEl.textContent = "Queuing build…";
  try {
    const job = await createJob({ kind: "setup", area: shapeToArea() });
    window.location.assign(runWatchUrl(job.id));
  } catch (err) {
    buildBtn.disabled = false;
    statusEl.textContent = `Could not queue build: ${err.message ?? err}`;
  }
});

configureBtn.addEventListener("click", () => {
  if (!center || configureBtn.disabled) return;
  openConfigForm(shapeToArea());
});

async function loadRegions() {
  try {
    drawRegions(await listRegions());
  } catch {
    statusEl.textContent = "Could not load cached regions.";
  }
}

// Re-run with tweaks (Story 3.2): arriving as `/?rerun=<job_id>` opens the query
// config form directly on the source run's stored area + params — bypassing the
// map picker (the area is taken verbatim from the record, whatever its shape;
// coverage isn't re-checked here — a since-cleared cache just fails the query
// gracefully at run time). The param is cleared afterward so a refresh doesn't
// re-trigger. Submit mints a brand-new job (createJob), so the original run is
// untouched.
async function handleRerun() {
  const jobId = new URLSearchParams(location.search).get("rerun");
  if (!jobId) return;
  history.replaceState(null, "", location.pathname);
  try {
    const job = await getJob(jobId);
    await openConfigForm(job.area, job.params);
  } catch (err) {
    statusEl.textContent = `Could not load run to re-run: ${err.message ?? err}`;
  }
}

void loadRegions();
void handleRerun();
