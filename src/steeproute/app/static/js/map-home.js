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

import { createJob, listRegions, resolveArea, runWatchUrl } from "./api.js";

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
let resolveSeq = 0; // drop out-of-order resolve responses

// A draggable HTML handle — a divIcon needs no marker-image asset (the vendored
// Leaflet ships JS/CSS only, like the CLI report which uses no markers).
const handleIcon = L.divIcon({ className: "map-handle", iconSize: [16, 16] });

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

function drawRegions(regions) {
  for (const r of regions) {
    L.rectangle(boundsToLatLngs(r.bounds), {
      className: "region-overlay",
      color: "#3a923f",
      weight: 2,
      fillOpacity: 0.12,
    }).addTo(map);
  }
}

map.on("click", (ev) => {
  center = { lat: ev.latlng.lat, lon: ev.latlng.lng };
  ensureHandle();
  handleMarker.setLatLng([center.lat, center.lon]);
  void resolveAndRender(DEFAULT_RADIUS_KM, { moveHandle: true });
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
  // Placeholder: the config form + query kind land in Epic 2 (Story 2.1).
  statusEl.textContent = "Configure query arrives in Epic 2.";
});

async function loadRegions() {
  try {
    drawRegions(await listRegions());
  } catch {
    statusEl.textContent = "Could not load cached regions.";
  }
}

void loadRegions();
