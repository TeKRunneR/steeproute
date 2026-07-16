// Single fetch/EventSource wrapper for the web App frontend.
//
// architecture-app.md §Frontend conventions: this is the ONLY file that hardcodes
// endpoint URLs. Every other module talks to the backend through these functions,
// so an API change touches one place. snake_case is read straight off the wire —
// no camelCase translation layer.

async function _json(path, options) {
  const resp = await fetch(path, options);
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      detail = (await resp.json()).detail ?? detail;
    } catch {
      /* non-JSON body — keep the status text */
    }
    throw new ApiError(resp.status, detail);
  }
  return resp.json();
}

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
  }
}

/** All jobs, creation-ordered (the run registry). */
export function listJobs() {
  return _json("/jobs");
}

/** Built regions for the map overlay (each has center, radius_km, bounds). */
export function listRegions() {
  return _json("/regions");
}

/** The introspected query config-form schema (each field has name, type,
 *  default, help, group, choices) — the single source `config-form.js`
 *  renders from; no flag names are hand-listed in JS. */
export function getQuerySchema() {
  return _json("/params/query-schema");
}

/** Resolve a picked area to its server-computed bbox + coverage decision
 *  ({center, radius_km, bounds, covered, cache_key_hash}). The server owns all
 *  km→deg + containment, so the client never re-derives geometry. */
export function resolveArea(lat, lon, radiusKm) {
  const q = new URLSearchParams({ lat, lon, radius_km: radiusKm });
  return _json(`/regions/resolve?${q}`);
}

/** Enqueue a job (e.g. `{kind:"setup", area:{center,radius_km}}`). Returns the
 *  created record (201) with its id; throws ApiError(422) on invalid params. */
export function createJob(body) {
  return _json("/jobs", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

/** One job record, or throws ApiError(404) for an unknown id. */
export function getJob(jobId) {
  return _json(`/jobs/${jobId}`);
}

/** Hard-cancel a running job. Throws ApiError(409) if it is not running. */
export function stopJob(jobId) {
  return _json(`/jobs/${jobId}/stop`, { method: "POST" });
}

/** Cancel a queued job (App Story 3.2): DELETE it from the store so it never
 *  runs. Resolves on 204; throws ApiError(409) if it is not queued (a running
 *  job is stopped, not cancelled), ApiError(404) for an unknown id. No JSON body
 *  to parse on success, so this doesn't go through `_json`. */
export async function cancelJob(jobId) {
  const resp = await fetch(`/jobs/${jobId}`, { method: "DELETE" });
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      detail = (await resp.json()).detail ?? detail;
    } catch {
      /* non-JSON body — keep the status text */
    }
    throw new ApiError(resp.status, detail);
  }
}

/** Open the job's SSE progress stream. Caller listens for `progress`/`status`
 *  named events and calls `.close()` when done. */
export function openJobEvents(jobId) {
  return new EventSource(`/jobs/${jobId}/events`);
}

/** The Run-watch URL for a job (kept here so URL shape lives in one place). */
export function runWatchUrl(jobId) {
  return `/runs/${jobId}`;
}

/** The Result-view (S5) page URL for a job. */
export function resultViewUrl(jobId) {
  return `/runs/${jobId}/result`;
}

/** The Map-home URL that opens the query config form prefilled from a past run's
 *  stored area + params (App Story 3.2 — re-run with tweaks). `map-home.js` reads
 *  the `?rerun` id, fetches the job, and opens the form; a new job is enqueued on
 *  submit (the source run is untouched). */
export function rerunConfigUrl(jobId) {
  return `/?rerun=${encodeURIComponent(jobId)}`;
}

/** The route reports a done query produced, as `{index, filename}` objects in
 *  numeric order. Throws ApiError(404) for a job with no viewable result; `[]`
 *  if it produced none. */
export function listRoutes(jobId) {
  return _json(`/jobs/${jobId}/routes`);
}

/** Human-readable message for a failed `getJob` (shared by the run-watch and
 *  result views, which both surface it their own way). */
export function jobLoadErrorMessage(err, jobId) {
  return err instanceof ApiError && err.status === 404
    ? `No such job: ${jobId}`
    : "Failed to load job.";
}

/** The static URL of one result file under the job's `result/` dir (for the
 *  iframe `src`). Serving is constrained server-side to `<job>/result/`. */
export function resultFileUrl(jobId, filename) {
  return `/jobs/${jobId}/result/${filename}`;
}
