// S1 Map home — pick a center+radius, see cached regions, build an uncached one
// (architecture-app.md §Category 6/10; UX S1/F2). Vanilla ES module, Leaflet from
// the vendored CLI copy, all backend calls through api.js (the only URL holder).
//
// The server is the single authority for geometry: the selection's WGS84 bbox
// and the green/grey coverage decision come from `GET /regions/resolve`, computed
// by the CLI cache's own km→deg conversion + containment. This file re-derives
// NEITHER — it passes the picked (center, radius_km) through and renders what the
// server returns, so the overlay can't drift from query-side coverage. The one
// km value comes from Leaflet's own `map.distance` (library geodesy), not a
// hand-copied conversion.

import { createJob, getJob, listRegions, resolveArea, runWatchUrl } from "./api.js";
import { openConfigForm } from "./config-form.js";

const DEFAULT_RADIUS_KM = 10;
const GRENOBLE = [45.19, 5.72];

// --- DOM ---------------------------------------------------------------------
const readoutEl = document.getElementById("selection-readout");
const centerEl = document.getElementById("sel-center");
const radiusEl = document.getElementById("sel-radius");
const coverageEl = document.getElementById("sel-coverage");
const buildBtn = document.getElementById("build-btn");
const configureBtn = document.getElementById("configure-btn");
const statusEl = document.getElementById("picker-status");
const hintEl = document.getElementById("picker-hint");
const modeControlEl = document.getElementById("mode-control");

// Selection modes (Story 4.1 / FR11). Exclusive: the map click only drops a
// center in area-pick; only in move-selection is the whole box draggable; only
// in select-region are the green overlays clickable. Per-mode hint copy too.
const MODE_HINTS = {
  "area-pick": "Click the map to drop a center, then drag the handle to set the radius.",
  "move-selection": "Drag the selection to reposition it — the radius stays the same.",
  "select-region": "Click a green built region to select it for querying.",
};

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
let radiusKm = DEFAULT_RADIUS_KM;
let selectionRect = null;
let handleMarker = null;
let centerMarker = null; // move-selection drag handle; present only in that mode
let mode = "area-pick";
let resolveSeq = 0; // drop out-of-order resolve responses

// A draggable HTML handle — a divIcon needs no marker-image asset (the vendored
// Leaflet ships JS/CSS only, like the CLI report which uses no markers).
const handleIcon = L.divIcon({ className: "map-handle", iconSize: [16, 16] });
const moveIcon = L.divIcon({ className: "map-move-handle", iconSize: [18, 18] });

function boundsToLatLngs(b) {
  return [
    [b.south, b.west],
    [b.north, b.east],
  ];
}

// Render the server-resolved selection: the exact bbox, coverage-driven styling,
// the readout, and the action-button states. `moveHandle` snaps the handle back
// onto the (authoritative) east edge; skipped mid-drag so it doesn't fight the
// user's cursor.
function applyResolution(res, { moveHandle }) {
  radiusKm = res.radius_km;
  const covered = res.covered;

  if (selectionRect) selectionRect.remove();
  selectionRect = L.rectangle(
    boundsToLatLngs(res.bounds),
    covered
      ? { color: "#3a923f", weight: 2, fillOpacity: 0.05 }
      : { color: "#8a94a6", weight: 2, dashArray: "6 4", fillOpacity: 0.05 },
  ).addTo(map);

  if (moveHandle && handleMarker) handleMarker.setLatLng([center.lat, res.bounds.east]);
  // Keep the move handle (when present) snapped to the authoritative center.
  if (centerMarker) centerMarker.setLatLng([center.lat, center.lon]);

  readoutEl.hidden = false;
  centerEl.textContent = `${center.lat.toFixed(4)}, ${center.lon.toFixed(4)}`;
  radiusEl.textContent = `${radiusKm.toFixed(1)} km`;
  coverageEl.textContent = covered ? "cached — ready to query" : "needs build";

  buildBtn.disabled = covered;
  configureBtn.disabled = !covered;
  configureBtn.title = covered ? "" : "Build this region first";
  statusEl.textContent = "";
}

async function resolveAndRender(km, { moveHandle }) {
  const seq = ++resolveSeq;
  try {
    const res = await resolveArea(center.lat, center.lon, km);
    if (seq === resolveSeq) applyResolution(res, { moveHandle }); // ignore stale
  } catch (err) {
    if (seq === resolveSeq) statusEl.textContent = `Could not resolve area: ${err.message ?? err}`;
  }
}

function ensureHandle() {
  if (handleMarker) return;
  handleMarker = L.marker([center.lat, center.lon], {
    icon: handleIcon,
    draggable: true,
  }).addTo(map);
  // Radius = distance from center to the dragged handle, measured by Leaflet's
  // own geodesy (metres). Resolve on release so the bbox/coverage snap to the
  // server's canonical geometry; Leaflet moves the handle freely during the drag.
  handleMarker.on("dragend", () => {
    const km = map.distance(L.latLng(center.lat, center.lon), handleMarker.getLatLng()) / 1000;
    void resolveAndRender(Math.max(0.5, km), { moveHandle: true });
  });
}

// The move-selection drag handle: a draggable center marker that translates the
// whole selection. During the drag the existing (server-authored) rectangle is
// shifted rigidly by the lat/lon delta — a pure translation, no km→deg derived
// in JS — then on release the bbox + coverage re-resolve from the server so the
// canonical geometry replaces the preview.
function ensureCenterMarker() {
  if (centerMarker || !center) return;
  centerMarker = L.marker([center.lat, center.lon], {
    icon: moveIcon,
    draggable: true,
  }).addTo(map);

  let start = null; // {lat, lon, sw, ne} captured at dragstart
  centerMarker.on("dragstart", () => {
    const b = selectionRect ? selectionRect.getBounds() : null;
    start = b ? { lat: center.lat, lon: center.lon, sw: b.getSouthWest(), ne: b.getNorthEast() } : null;
  });
  centerMarker.on("drag", () => {
    if (!start) return;
    const p = centerMarker.getLatLng();
    const dLat = p.lat - start.lat;
    const dLon = p.lng - start.lon;
    selectionRect.setBounds([
      [start.sw.lat + dLat, start.sw.lng + dLon],
      [start.ne.lat + dLat, start.ne.lng + dLon],
    ]);
    if (handleMarker) handleMarker.setLatLng([p.lat, start.ne.lng + dLon]);
  });
  centerMarker.on("dragend", () => {
    const p = centerMarker.getLatLng();
    center = { lat: p.lat, lon: p.lng };
    void resolveAndRender(radiusKm, { moveHandle: true }); // radius unchanged
  });
}

function removeCenterMarker() {
  if (!centerMarker) return;
  centerMarker.remove();
  centerMarker = null;
}

// Apply the interaction rules for the active mode. Exclusive by construction:
// the radius handle drags only in area-pick; the move handle exists only in
// move-selection; region overlays get the pointer cursor only in select-region.
function applyModeInteractivity() {
  if (handleMarker) {
    if (mode === "area-pick") handleMarker.dragging.enable();
    else handleMarker.dragging.disable();
  }
  if (mode === "move-selection") ensureCenterMarker();
  else removeCenterMarker();
  map.getContainer().classList.toggle("select-region-active", mode === "select-region");
  hintEl.textContent = MODE_HINTS[mode];
}

function drawRegions(regions) {
  for (const r of regions) {
    const rect = L.rectangle(boundsToLatLngs(r.bounds), {
      className: "region-overlay",
      color: "#3a923f",
      weight: 2,
      fillOpacity: 0.12,
    }).addTo(map);
    // select-region: snap the selection to this built region's exact geometry
    // (server-authored) and let coverage re-resolve → "Configure query" enabled.
    // Inert in the other modes (the mode guard returns early).
    rect.on("click", (ev) => {
      if (mode !== "select-region") return;
      L.DomEvent.stopPropagation(ev); // don't also fall through to the map click
      center = { lat: r.center[0], lon: r.center[1] };
      ensureHandle();
      handleMarker.setLatLng([center.lat, center.lon]);
      applyModeInteractivity(); // keep the freshly-created handle non-draggable here
      void resolveAndRender(r.radius_km, { moveHandle: true });
    });
  }
}

map.on("click", (ev) => {
  if (mode !== "area-pick") return; // only area-pick drops a new center
  center = { lat: ev.latlng.lat, lon: ev.latlng.lng };
  ensureHandle();
  handleMarker.setLatLng([center.lat, center.lon]);
  applyModeInteractivity(); // handle is draggable in area-pick; no move marker
  void resolveAndRender(DEFAULT_RADIUS_KM, { moveHandle: true });
});

modeControlEl.addEventListener("change", (ev) => {
  if (ev.target.name !== "map-mode") return;
  mode = ev.target.value;
  applyModeInteractivity();
});

buildBtn.addEventListener("click", async () => {
  if (!center) return;
  buildBtn.disabled = true;
  statusEl.textContent = "Queuing build…";
  try {
    const job = await createJob({
      kind: "setup",
      area: { center: [center.lat, center.lon], radius_km: radiusKm },
    });
    window.location.assign(runWatchUrl(job.id));
  } catch (err) {
    buildBtn.disabled = false;
    statusEl.textContent = `Could not queue build: ${err.message ?? err}`;
  }
});

configureBtn.addEventListener("click", () => {
  if (!center || configureBtn.disabled) return;
  openConfigForm({ center: [center.lat, center.lon], radius_km: radiusKm });
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
// map picker (the area is taken verbatim from the record; coverage isn't
// re-checked here — a since-cleared cache just fails the query gracefully at run
// time). The param is cleared afterward so a refresh doesn't re-trigger. Submit
// mints a brand-new job (createJob), so the original run is untouched.
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
