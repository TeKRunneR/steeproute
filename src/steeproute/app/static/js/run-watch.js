// S3 Run-watch (architecture-app.md §Category 10; UX spec §S3).
//
// The crux screen, rendered flavour-agnostically: phase / stage (n/total) /
// auto-scrolling log tail, fed by the job's SSE stream (snapshot then live tail,
// stitched server-side). A Stop control hard-cancels a running job; on the
// terminal `status` event the stream closes and a status-appropriate footer
// replaces the controls. The GRASP best-cost/iteration line appears only when a
// progress frame carries `grasp` (query solve phase, Story 2.2) — absent for
// setup, never reserved.

import {
  getJob,
  stopJob,
  openJobEvents,
  resultViewUrl,
  jobLoadErrorMessage,
  ApiError,
} from "./api.js";

const jobId = decodeURIComponent(location.pathname.split("/").pop() ?? "");

const $ = (id) => document.getElementById(id);
const identityEl = $("job-identity");
const statusEl = $("job-status");
const phaseEl = $("progress-phase");
const stageEl = $("progress-stage");
const graspEl = $("progress-grasp");
const logEl = $("log-tail");
const stopBtn = $("stop-btn");
const footerEl = $("run-footer");

let startedAt = null;
let elapsedTimer = null;
let terminal = false;
let jobKind = null;

function fmtElapsed(ms) {
  const total = Math.max(0, Math.floor(ms / 1000));
  const mm = String(Math.floor(total / 60)).padStart(2, "0");
  const ss = String(total % 60).padStart(2, "0");
  return `${mm}:${ss}`;
}

function renderStatus(status) {
  let line = `status: ${status.toUpperCase()}`;
  if (startedAt) {
    const started = new Date(startedAt);
    line += `  ·  started ${started.toLocaleTimeString()}`;
    if (!terminal) line += `  ·  elapsed ${fmtElapsed(Date.now() - started.getTime())}`;
  }
  statusEl.textContent = line;
}

function renderIdentity(job) {
  const [lat, lon] = job.area?.center ?? [];
  const radius = job.area?.radius_km;
  const center = lat != null && lon != null ? `${lat}, ${lon}` : "?";
  identityEl.textContent = `${job.kind} · r${radius ?? "?"}  (center ${center} · radius ${radius ?? "?"} km)`;
}

function renderProgress(model) {
  phaseEl.textContent = `PHASE: ${model.phase ?? "—"}`;
  const name = model.stage_name ?? "—";
  const total = model.stage_total ?? 0;
  stageEl.textContent = total
    ? `STAGE: ${name}  (stage ${model.stage_index} / ${total})`
    : `STAGE: ${name}`;
  // grasp is present-as-null for setup; only render the line when populated.
  if (model.grasp) {
    graspEl.hidden = false;
    graspEl.textContent = `best-so-far cost: ${model.grasp.best_cost}  ·  iteration: ${model.grasp.iter}`;
  } else {
    graspEl.hidden = true;
    graspEl.textContent = "";
  }
  if (Array.isArray(model.log_tail)) {
    logEl.textContent = model.log_tail.join("\n");
    logEl.scrollTop = logEl.scrollHeight; // auto-scroll to newest
  }
}

function showFooter(status, exitCode) {
  terminal = true;
  stopBtn.hidden = true;
  if (elapsedTimer) clearInterval(elapsedTimer);
  footerEl.hidden = false;
  footerEl.innerHTML = "";
  // A failed job shows its exit code + a Re-run affordance (prefill form is Epic
  // 3 — this is a placeholder link for now); a stopped (hard-cancelled) job has
  // no result (architecture-app.md §Category 7), so no View-routes there.
  if (status === "failed") {
    const code = exitCode != null ? ` (exit code ${exitCode})` : "";
    footerEl.append(document.createTextNode(`failed${code} · `));
    const rerun = document.createElement("a");
    rerun.href = "/";
    rerun.textContent = "Re-run with tweaks";
    footerEl.appendChild(rerun);
  } else if (status === "stopped") {
    footerEl.textContent = "stopped · no result";
  } else if (status === "done" && jobKind === "query") {
    // A done query produced the CLI route report(s) — offer the S5 iframe view
    // (Story 2.3). A done setup job renders nothing, so it gets a plain marker.
    footerEl.append(document.createTextNode("done · "));
    const view = document.createElement("a");
    view.href = resultViewUrl(jobId);
    view.textContent = "View routes";
    footerEl.appendChild(view);
  } else if (status === "done") {
    footerEl.textContent = "done";
  }
}

async function wireStop() {
  stopBtn.addEventListener("click", async () => {
    stopBtn.disabled = true;
    try {
      await stopJob(jobId);
    } catch (err) {
      stopBtn.disabled = false;
      if (err instanceof ApiError && err.status === 409) return; // already terminal
      throw err;
    }
  });
}

async function init() {
  if (!jobId) {
    identityEl.textContent = "No job id in URL.";
    return;
  }
  let job;
  try {
    job = await getJob(jobId);
  } catch (err) {
    identityEl.textContent = jobLoadErrorMessage(err, jobId);
    return;
  }
  renderIdentity(job);
  startedAt = job.started_at;
  jobKind = job.kind;
  renderStatus(job.status);

  const alreadyTerminal = ["done", "failed", "stopped"].includes(job.status);
  if (!alreadyTerminal) {
    stopBtn.hidden = false;
    void wireStop();
    elapsedTimer = setInterval(() => renderStatus(job.status), 1000);
  }

  const stream = openJobEvents(jobId);
  stream.addEventListener("progress", (ev) => {
    try {
      renderProgress(JSON.parse(ev.data));
    } catch {
      /* skip a malformed frame */
    }
  });
  stream.addEventListener("status", (ev) => {
    let payload = {};
    try {
      payload = JSON.parse(ev.data);
    } catch {
      /* keep defaults */
    }
    renderStatus(payload.status ?? job.status);
    showFooter(payload.status, payload.exit_code);
    stream.close();
  });
}

void init();
