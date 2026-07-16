// S5 Result view (architecture-app.md §Category 10; UX spec §S5).
//
// Embeds the run's existing CLI `route-<i>.html` report in an iframe as-is — no
// native re-render (FR8). A query returns up to N routes as separate files, so a
// small selector switches the iframe `src` between them; the file list comes from
// the server (GET /jobs/{id}/routes), never an assumed count. Reached from the
// S3 Run-watch "View routes" action (and, later, the Epic-3 run library).

import {
  getJob,
  listRoutes,
  resultFileUrl,
  runWatchUrl,
  jobLoadErrorMessage,
  ApiError,
} from "./api.js";

// URL shape is /runs/<id>/result — the id is the segment before "result".
const parts = location.pathname.split("/").filter(Boolean); // ["runs", "<id>", "result"]
const jobId = decodeURIComponent(parts[1] ?? "");

const $ = (id) => document.getElementById(id);
const selectorEl = $("route-selector");
const statusEl = $("result-status");
const frameEl = $("route-frame");
const backEl = $("back-to-run");

function showMessage(text) {
  statusEl.textContent = text;
  frameEl.hidden = true;
}

function selectRoute(filename, button) {
  frameEl.hidden = false;
  frameEl.src = resultFileUrl(jobId, filename);
  for (const b of selectorEl.querySelectorAll("button")) {
    b.classList.toggle("active", b === button);
  }
}

function renderSelector(routes) {
  selectorEl.innerHTML = "";
  // Each route is {index, filename} from the server — the index is already
  // parsed there, so the label needs no filename re-parsing here.
  routes.forEach((route, i) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "route-tab";
    btn.textContent = `Route ${route.index}`;
    btn.addEventListener("click", () => selectRoute(route.filename, btn));
    selectorEl.appendChild(btn);
    if (i === 0) selectRoute(route.filename, btn);
  });
}

async function init() {
  if (!jobId) {
    showMessage("No job id in URL.");
    return;
  }
  backEl.href = runWatchUrl(jobId);

  let job;
  try {
    job = await getJob(jobId);
  } catch (err) {
    showMessage(jobLoadErrorMessage(err, jobId));
    return;
  }
  const [lat, lon] = job.area?.center ?? [];
  statusEl.textContent = `${job.kind} · r${job.area?.radius_km ?? "?"} (center ${lat ?? "?"}, ${lon ?? "?"})`;

  let routes;
  try {
    routes = await listRoutes(jobId);
  } catch (err) {
    showMessage(
      err instanceof ApiError && err.status === 404
        ? "This run has no viewable routes."
        : "Failed to load routes.",
    );
    return;
  }
  if (!Array.isArray(routes) || routes.length === 0) {
    showMessage("This run produced no routes.");
    return;
  }
  renderSelector(routes);
}

void init();
