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

/** Open the job's SSE progress stream. Caller listens for `progress`/`status`
 *  named events and calls `.close()` when done. */
export function openJobEvents(jobId) {
  return new EventSource(`/jobs/${jobId}/events`);
}

/** The Run-watch URL for a job (kept here so URL shape lives in one place). */
export function runWatchUrl(jobId) {
  return `/runs/${jobId}`;
}
