// S2 Config form (App Story 2.1) — a schema-driven basic/advanced panel over
// the map, opened from map-home when "Configure query" is enabled. Rendered
// entirely from `GET /params/query-schema` (architecture-app.md §Category 9):
// this file hand-lists no query flag names, types, or defaults — the schema
// is the single source of truth for both this form and the App's `argv`
// build (`cli_adapter.params_schema` / `cli_adapter.argv.build_query_argv`).

import { createJob, getQuerySchema, runWatchUrl } from "./api.js";

const panelEl = document.getElementById("config-form");
const fieldsFormEl = document.getElementById("config-form-fields");
const cancelBtn = document.getElementById("config-cancel-btn");
const statusEl = document.getElementById("config-status");

let schemaCache = null;
let currentArea = null;
// Re-run-with-tweaks prefill (Story 3.2): a prior run's stored `params` dict, or
// null for a fresh "Configure query" open. A field takes its stored value when
// that value is non-null, otherwise the schema (quality-demo) default — so a
// re-run reproduces the run's effective config plus the user's explicit tweaks.
let currentPrefill = null;

async function loadSchema() {
  if (schemaCache === null) schemaCache = await getQuerySchema();
  return schemaCache;
}

function fieldInputId(field) {
  return `qf-${field.name}`;
}

/** The value a field should show: the prefill's stored value when non-null,
 *  otherwise the schema default (Story 3.2 re-run-with-tweaks). */
function effectiveValue(field) {
  const pv = currentPrefill?.[field.name];
  return pv !== null && pv !== undefined ? pv : field.default;
}

function buildInput(field) {
  const value = effectiveValue(field);
  let input;
  if (field.type === "bool") {
    input = document.createElement("input");
    input.type = "checkbox";
    input.checked = Boolean(value);
  } else if (field.type === "choice") {
    input = document.createElement("select");
    for (const choice of field.choices ?? []) {
      const opt = document.createElement("option");
      opt.value = choice;
      opt.textContent = choice;
      if (choice === value) opt.selected = true;
      input.appendChild(opt);
    }
  } else {
    input = document.createElement("input");
    input.type = field.type === "string" ? "text" : "number";
    if (field.type === "float") input.step = "any";
    if (value !== null && value !== undefined) input.value = value;
  }
  input.id = fieldInputId(field);
  input.name = field.name;
  return input;
}

function buildFieldRow(field) {
  const row = document.createElement("label");
  row.className = "config-field";
  row.htmlFor = fieldInputId(field);
  const labelText = document.createElement("span");
  labelText.className = "config-field-label";
  labelText.textContent = field.name.replaceAll("_", " ");
  if (field.help) labelText.title = field.help;
  row.appendChild(labelText);
  row.appendChild(buildInput(field));
  return row;
}

function buildGroup(fields) {
  const wrap = document.createElement("div");
  wrap.className = "config-group";
  for (const field of fields) wrap.appendChild(buildFieldRow(field));
  return wrap;
}

async function renderForm() {
  fieldsFormEl.innerHTML = "";
  const schema = await loadSchema();
  const basic = schema.filter((f) => f.group === "basic");
  const advanced = schema.filter((f) => f.group === "advanced");

  fieldsFormEl.appendChild(buildGroup(basic));

  const details = document.createElement("details");
  details.className = "config-advanced";
  const summary = document.createElement("summary");
  summary.textContent = "Advanced";
  details.appendChild(summary);
  details.appendChild(buildGroup(advanced));
  fieldsFormEl.appendChild(details);
}

function readParams(schema) {
  const params = {};
  for (const field of schema) {
    const input = document.getElementById(fieldInputId(field));
    if (!input) continue;
    if (field.type === "bool") {
      params[field.name] = input.checked;
    } else if (field.type === "int") {
      params[field.name] = input.value === "" ? null : Number.parseInt(input.value, 10);
    } else if (field.type === "float") {
      params[field.name] = input.value === "" ? null : Number.parseFloat(input.value);
    } else {
      params[field.name] = input.value === "" ? null : input.value;
    }
  }
  return params;
}

/** Open the panel for an area (`{center: [lat, lon], radius_km}`, matching
 *  `AreaSpec`). `prefill` is optional: a prior run's stored `params` dict for
 *  re-run-with-tweaks (Story 3.2), or omitted for a fresh "Configure query"
 *  (all fields show their quality-demo defaults). Re-renders the form from the
 *  schema every time (cheap; keeps prefill logic in one place).
 *
 *  The panel is revealed only AFTER the fields are rendered — never before. The
 *  Queue button lives inside `#config-form` (hidden until then), so a submit
 *  can't fire against an empty form during the schema fetch and silently queue
 *  a job with default-only `params`. */
export async function openConfigForm(area, prefill = null) {
  currentArea = area;
  currentPrefill = prefill;
  statusEl.textContent = "";
  try {
    await renderForm();
  } catch (err) {
    // Reveal the panel so the failure is visible; the submit guard below keeps
    // an un-rendered (fieldless) form from queuing anything.
    statusEl.textContent = `Could not load query options: ${err.message ?? err}`;
  }
  panelEl.hidden = false;
}

export function closeConfigForm() {
  panelEl.hidden = true;
}

fieldsFormEl.addEventListener("submit", (ev) => {
  ev.preventDefault();
  if (!currentArea) return;
  // Guard: never submit before the fields have rendered (schema still loading,
  // or its fetch failed) — otherwise `readParams` finds no inputs and we'd
  // silently queue an all-default query. The rendered form always has fields.
  if (fieldsFormEl.querySelector("input, select") === null) {
    statusEl.textContent = "Query options are still loading — try again in a moment.";
    return;
  }
  void (async () => {
    const schema = await loadSchema();
    const params = readParams(schema);
    statusEl.textContent = "Queuing query…";
    try {
      const job = await createJob({ kind: "query", area: currentArea, params });
      window.location.assign(runWatchUrl(job.id));
    } catch (err) {
      statusEl.textContent = `Could not queue query: ${err.message ?? err}`;
    }
  })();
});

cancelBtn.addEventListener("click", () => {
  closeConfigForm();
});
