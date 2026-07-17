// S2 Config form (App Story 2.1; flat layout in Story app-4-2) — a schema-driven
// panel over the map, opened from map-home when "Configure query" is enabled.
// Rendered entirely from `GET /params/query-schema` (architecture-app.md
// §Category 9): this file hand-lists no query flag names, types, or defaults —
// the schema is the single source of truth for both this form and the App's
// `argv` build (`cli_adapter.params_schema` / `cli_adapter.argv.build_query_argv`).
// Every field renders in one always-visible list (no basic/advanced collapse):
// nearly every query flag matters for this tool, so hiding most behind a toggle
// only added a click.

import { createJob, getQuerySchema, runWatchUrl } from "./api.js";
import { groupThousands, stripGrouping } from "./format.js";

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

/** Long numeric fields (iter budget, stagnation iters, area cap) get space
 *  thousands separators for readability (Story app-4-2 / FR14). A default with
 *  magnitude >= 1000 is the "long" signal — a self-contained display heuristic,
 *  not a hand-listed flag set, so it tracks the schema. Small numbers (theta, n,
 *  workers…) stay ordinary number inputs with their native spinner. */
function isGroupedNumberField(field) {
  return (
    (field.type === "int" || field.type === "float") &&
    typeof field.default === "number" &&
    Math.abs(field.default) >= 1000
  );
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
  } else if (isGroupedNumberField(field)) {
    // Space-grouped text input: native number inputs render no separator, so a
    // long value must be a text field. Grouping is display-only — `readParams`
    // strips it back to a plain number before it reaches the wire / argv.
    input = document.createElement("input");
    input.type = "text";
    input.inputMode = "numeric";
    if (value !== null && value !== undefined) input.value = groupThousands(value);
    input.addEventListener("blur", () => {
      input.value = groupThousands(stripGrouping(input.value));
    });
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
  // Flat form (Story app-4-2): every field in one always-visible list, in the
  // schema's introspection order (i.e. the CLI's option declaration order).
  fieldsFormEl.appendChild(buildGroup(schema));
}

/** Read every input into a params dict. Returns `{ params, invalid }`; `invalid`
 *  names any numeric field whose text isn't a clean number, so the submit handler
 *  can block rather than silently queue a wrong value. */
function readParams(schema) {
  const params = {};
  const invalid = [];
  for (const field of schema) {
    const input = document.getElementById(fieldInputId(field));
    if (!input) continue;
    if (field.type === "bool") {
      params[field.name] = input.checked;
    } else if (field.type === "int" || field.type === "float") {
      // Strip display grouping, then parse with `Number` (NOT parseInt/parseFloat):
      // `Number` rejects a mistyped separator or stray char as NaN, whereas
      // `parseInt("1,000", 10)` truncates to 1 — a valid-looking wrong value that
      // would silently queue e.g. a 1-iteration solve. An empty field stays unset
      // (null → schema default); a non-numeric entry is flagged, not coerced.
      const plain = stripGrouping(input.value);
      if (plain === "") {
        params[field.name] = null;
      } else {
        const n = Number(plain);
        if (Number.isNaN(n)) invalid.push(field.name);
        else params[field.name] = n;
      }
    } else {
      params[field.name] = input.value === "" ? null : input.value;
    }
  }
  return { params, invalid };
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
    const { params, invalid } = readParams(schema);
    if (invalid.length > 0) {
      // Block instead of silently reverting a mistyped number to its default.
      statusEl.textContent = `Enter a valid number for: ${invalid
        .map((name) => name.replaceAll("_", " "))
        .join(", ")}`;
      return;
    }
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
