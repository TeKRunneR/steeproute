// S4 Run library (architecture-app.md §Category 8/10; UX spec §S4).
//
// One list of every job, ordered running → queued (in order) → history (newest
// first), rendered from the existing creation-ordered `GET /jobs` (no new
// endpoint — the ordering is a display regrouping here, not a server change).
// Each card shows `kind · area-label`, center/radius, the created timestamp, the
// status, and a status-appropriate metric (a done query's objective/cost, a
// failed job's exit code). Only the navigational actions with a real
// destination are wired — Watch (running) and View routes (done query); Cancel
// (queued) and Re-run with tweaks (finished/failed) land in Story 3.2.

import { listJobs, runWatchUrl, resultViewUrl } from "./api.js";

const listEl = document.getElementById("runs-list");
const emptyEl = document.getElementById("runs-empty");

const TERMINAL = ["done", "failed", "stopped"];

/** running → queued (creation-asc = queue order) → terminal (newest first).
 *  `jobs` arrives creation-ascending from `GET /jobs`. */
function orderForLibrary(jobs) {
  const running = jobs.filter((j) => j.status === "running");
  const queued = jobs.filter((j) => j.status === "queued");
  const history = jobs.filter((j) => TERMINAL.includes(j.status)).reverse();
  return [...running, ...queued, ...history];
}

function areaLabel(job) {
  const radius = job.area?.radius_km;
  return `${job.kind} · r${radius ?? "?"}`;
}

function metaText(job) {
  const [lat, lon] = job.area?.center ?? [];
  const radius = job.area?.radius_km;
  const center = lat != null && lon != null ? `${lat}, ${lon}` : "?";
  const when = job.created_at ? new Date(job.created_at).toLocaleString() : "?";
  return `center ${center} · radius ${radius ?? "?"} km · ${when}`;
}

/** The status-appropriate metric line, or "" when there is none to show. */
function metricText(job) {
  if (job.kind === "query" && job.status === "done" && job.result_objective != null) {
    return `cost ${job.result_objective}`;
  }
  if (job.status === "failed") {
    const code = job.exit_code != null ? `exit code ${job.exit_code}` : "failed";
    // A boot-interrupted job carries failure_reason="interrupted" (Story 3.3).
    return job.failure_reason ? `${code} · ${job.failure_reason}` : code;
  }
  return "";
}

function addAction(container, href, label) {
  const link = document.createElement("a");
  link.href = href;
  link.className = "run-card-action";
  link.textContent = label;
  container.appendChild(link);
}

function renderCard(job) {
  const card = document.createElement("li");
  card.className = `run-card run-card--${job.status}`;

  const head = document.createElement("div");
  head.className = "run-card-head";
  const title = document.createElement("span");
  title.className = "run-card-title";
  title.textContent = areaLabel(job);
  const status = document.createElement("span");
  status.className = `run-card-status status-${job.status}`;
  status.textContent = job.status;
  head.append(title, status);
  card.appendChild(head);

  const meta = document.createElement("div");
  meta.className = "run-card-meta";
  meta.textContent = metaText(job);
  card.appendChild(meta);

  const metric = metricText(job);
  if (metric) {
    const metricEl = document.createElement("div");
    metricEl.className = "run-card-metric";
    metricEl.textContent = metric;
    card.appendChild(metricEl);
  }

  const actions = document.createElement("div");
  actions.className = "run-card-actions";
  if (job.status === "running") addAction(actions, runWatchUrl(job.id), "Watch");
  if (job.status === "done" && job.kind === "query") {
    addAction(actions, resultViewUrl(job.id), "View routes");
  }
  // Cancel (queued) + Re-run with tweaks (finished/failed) are Story 3.2.
  if (actions.childElementCount > 0) card.appendChild(actions);

  return card;
}

async function init() {
  let jobs;
  try {
    jobs = await listJobs();
  } catch {
    emptyEl.hidden = false;
    emptyEl.textContent = "Failed to load runs.";
    return;
  }
  const ordered = orderForLibrary(jobs);
  if (ordered.length === 0) {
    emptyEl.hidden = false;
    return;
  }
  emptyEl.hidden = true;
  listEl.replaceChildren(...ordered.map(renderCard));
}

void init();
