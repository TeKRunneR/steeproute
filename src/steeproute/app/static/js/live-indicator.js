// Global header live-job indicator (architecture-app.md §Category 10; UX-DR6).
//
// Present on every screen: it finds the currently-running job by polling
// `GET /jobs` (the fallback), subscribes to that job's SSE for live stage updates,
// renders a compact label, and links back to its Run-watch — making
// "return to live progress at any time" real without a modal. Empty when idle.

import { listJobs, openJobEvents, runWatchUrl } from "./api.js";

const POLL_MS = 4000;

const el = document.getElementById("live-indicator");
let currentId = null;
let stream = null;

function clear() {
  detachStream();
  currentId = null;
  if (el) el.textContent = "";
}

function detachStream() {
  if (stream) {
    stream.close();
    stream = null;
  }
}

function render(job, model) {
  if (!el) return;
  const radius = job.area?.radius_km;
  const area = radius != null ? `r${radius}` : "area";
  const stage = model?.stage_name ? ` · ${model.stage_name}` : "";
  el.innerHTML = "";
  const link = document.createElement("a");
  link.href = runWatchUrl(job.id);
  link.className = "live-indicator-link";
  link.textContent = `● ${job.kind} running · ${area}${stage}`;
  el.appendChild(link);
}

function attach(job) {
  detachStream();
  currentId = job.id;
  render(job, null);
  stream = openJobEvents(job.id);
  stream.addEventListener("progress", (ev) => {
    try {
      render(job, JSON.parse(ev.data));
    } catch {
      /* ignore a malformed frame — the next one refreshes the label */
    }
  });
  // On terminal, the job is no longer running: drop the stream and re-poll so a
  // newly-started job (serial queue) takes over the slot.
  stream.addEventListener("status", () => {
    detachStream();
    void refresh();
  });
}

async function refresh() {
  let jobs;
  try {
    jobs = await listJobs();
  } catch {
    return; // transient; the next tick retries
  }
  const running = jobs.find((job) => job.status === "running");
  if (!running) {
    clear();
    return;
  }
  if (running.id !== currentId) attach(running);
}

void refresh();
setInterval(() => void refresh(), POLL_MS);
