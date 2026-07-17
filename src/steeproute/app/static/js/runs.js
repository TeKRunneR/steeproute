// S4 Run library (architecture-app.md §Category 8/10; UX spec §S4).
//
// One list of every job, ordered running → queued (in order) → history (newest
// first), rendered from the existing creation-ordered `GET /jobs` (no new
// endpoint — the ordering is a display regrouping here, not a server change).
// Each card LEADS with the run's human `area_label` (a reverse-geocoded town/
// place name, Story 4.3), falling back to `kind · r{radius}` when unlabelled;
// center/radius/timestamp are secondary detail, plus a status-appropriate metric
// (a done query's objective/cost, a failed job's exit code) and — for query runs
// only — a click-to-reveal view of the stored params (Story 4.3). Actions are
// status-gated: Watch (running), View routes (done query), Cancel (queued →
// DELETE, Story 3.2), and Re-run with tweaks (done/failed query, Story 3.2).

import { listJobs, cancelJob, runWatchUrl, resultViewUrl, rerunConfigUrl, ApiError } from "./api.js";
import { groupThousands } from "./format.js";

const listEl = document.getElementById("runs-list");
const emptyEl = document.getElementById("runs-empty");
const statusEl = document.getElementById("runs-status");

const TERMINAL = ["done", "failed", "stopped"];

/** running → queued (creation-asc = queue order) → terminal (newest first).
 *  `jobs` arrives creation-ascending from `GET /jobs`. */
function orderForLibrary(jobs) {
  const running = jobs.filter((j) => j.status === "running");
  const queued = jobs.filter((j) => j.status === "queued");
  const history = jobs.filter((j) => TERMINAL.includes(j.status)).reverse();
  return [...running, ...queued, ...history];
}

/** The card's lead identifier: the human town/place label when present, else
 *  today's `kind · r{radius}` fallback (Story 4.3 — a run with no `area_label`,
 *  i.e. geocoding disabled/offline/no place, is never worse off than before). */
function cardTitle(job) {
  const radius = job.area?.radius_km;
  if (job.area_label) return `${job.kind} · ${job.area_label}`;
  return `${job.kind} · r${radius ?? "?"}`;
}

/** Secondary detail: the raw center/radius (now that the label leads) + timestamp. */
function metaText(job) {
  const [lat, lon] = job.area?.center ?? [];
  const radius = job.area?.radius_km;
  const center = lat != null && lon != null ? `${lat}, ${lon}` : "?";
  const when = job.created_at ? new Date(job.created_at).toLocaleString() : "?";
  return `center ${center} · radius ${radius ?? "?"} km · ${when}`;
}

/** Display a stored param value: booleans as on/off, long numbers space-grouped
 *  (Story 4.2's `format.js`, matching the config form), null/unset as an em dash. */
function formatParamValue(value) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "on" : "off";
  if (typeof value === "number" && Math.abs(value) >= 1000) return groupThousands(value);
  return String(value);
}

/** A click-to-reveal (native <details>) view of a query run's full stored params
 *  (Story 4.3). Data is already on the job record (`job.params`) — no new fetch. */
function buildParamsView(job) {
  const details = document.createElement("details");
  details.className = "run-card-params";
  const summary = document.createElement("summary");
  summary.textContent = "Parameters";
  details.appendChild(summary);
  const dl = document.createElement("dl");
  dl.className = "run-card-params-list";
  for (const [name, value] of Object.entries(job.params ?? {})) {
    const dt = document.createElement("dt");
    dt.textContent = name.replaceAll("_", " ");
    const dd = document.createElement("dd");
    dd.textContent = formatParamValue(value);
    dl.append(dt, dd);
  }
  details.appendChild(dl);
  return details;
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

function addButton(container, label, className, onClick) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = `run-card-action ${className}`;
  btn.textContent = label;
  btn.addEventListener("click", onClick);
  container.appendChild(btn);
}

/** Re-run with tweaks is offered on query runs only (done or failed): a setup
 *  job has no query config form to prefill — a failed build is redone from the
 *  map (deliberate two-step). */
function offersRerun(job) {
  return job.kind === "query" && (job.status === "done" || job.status === "failed");
}

/** Cancel a queued job, then reload the list so it reflects the server. On a
 *  race (the worker just started it → 409, or it's already gone → 404) the reload
 *  still shows the truth (the job as running, or gone). The status message goes
 *  to `#runs-status`, which sits outside `#runs-list` so `load()`'s re-render
 *  doesn't wipe it — otherwise a failed cancel would reload silently. */
async function cancelAndReload(job, card) {
  for (const b of card.querySelectorAll("button")) b.disabled = true;
  try {
    await cancelJob(job.id);
    statusEl.hidden = true;
    statusEl.textContent = "";
  } catch (err) {
    statusEl.textContent =
      err instanceof ApiError && err.status === 409
        ? "Too late to cancel — the job already started."
        : `Could not cancel: ${err.message ?? err}`;
    statusEl.hidden = false;
  }
  await load();
}

function renderCard(job) {
  const card = document.createElement("li");
  card.className = `run-card run-card--${job.status}`;

  const head = document.createElement("div");
  head.className = "run-card-head";
  const title = document.createElement("span");
  title.className = "run-card-title";
  title.textContent = cardTitle(job);
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

  // Query runs expose their stored config on demand; setup params are trivial.
  if (job.kind === "query" && job.params && Object.keys(job.params).length > 0) {
    card.appendChild(buildParamsView(job));
  }

  const actions = document.createElement("div");
  actions.className = "run-card-actions";
  if (job.status === "running") addAction(actions, runWatchUrl(job.id), "Watch");
  if (job.status === "done" && job.kind === "query") {
    addAction(actions, resultViewUrl(job.id), "View routes");
  }
  if (offersRerun(job)) addAction(actions, rerunConfigUrl(job.id), "Re-run with tweaks");
  if (job.status === "queued") {
    addButton(actions, "Cancel", "run-card-action--danger", () => void cancelAndReload(job, card));
  }
  if (actions.childElementCount > 0) card.appendChild(actions);

  return card;
}

async function load() {
  let jobs;
  try {
    jobs = await listJobs();
  } catch {
    listEl.replaceChildren();
    emptyEl.hidden = false;
    emptyEl.textContent = "Failed to load runs.";
    return;
  }
  const ordered = orderForLibrary(jobs);
  if (ordered.length === 0) {
    listEl.replaceChildren();
    emptyEl.hidden = false;
    return;
  }
  emptyEl.hidden = true;
  listEl.replaceChildren(...ordered.map(renderCard));
}

void load();
