// Shared display-format helpers. Number grouping; area
// summaries live here too so the four screens that name a run's
// area all describe a rotated box the same way.
//
// Long numeric values (iter
// budget, stagnation iters) are shown space-grouped for readability —
// `1 000 000`, never commas (a comma is the French decimal separator, so it
// would misread). Grouping is DISPLAY-ONLY: the value on the wire / in argv
// stays a plain number. Factored out of config-form.js so the run-library
// params view groups numbers the same way.

// A no-break space (U+00A0) is used as the thousands separator so the grouped
// text never wraps or collapses in an input/label.
const THIN_GROUP = " ";

/** Space-group the integer part of a numeric value for display, leaving any
 *  sign and decimal fraction untouched (`1000000 → "1 000 000"`,
 *  `100000.5 → "100 000.5"`, `0.2 → "0.2"`). Non-finite / empty input returns
 *  the empty string. Accepts a number or a plain (ungrouped) numeric string. */
export function groupThousands(value) {
  if (value === null || value === undefined || value === "") return "";
  const text = String(value).trim();
  if (text === "") return "";
  // Split sign, integer, and fractional parts; only the integer part is grouped.
  const match = /^([+-]?)(\d+)(\.\d+)?$/.exec(text);
  if (match === null) return text; // not a plain number — leave as-is
  const [, sign, intPart, fracPart = ""] = match;
  const grouped = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, THIN_GROUP);
  return `${sign}${grouped}${fracPart}`;
}

/** Trim a km/degree value for display: at most 2 decimals, no trailing zeros
 *  (`2 → "2"`, `1.5 → "1.5"`, `16.000001 → "16"`). Non-numeric input → "?". */
function trimNumber(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "?";
  return String(Math.round(n * 100) / 100);
}

/** Compact identity chip for a job's / region's area.
 *
 *  An area is a possibly-rotated rectangle, so a run can have
 *  no radius at all: the centered-square spelling renders `r10`, any other box
 *  renders its full dimensions and bearing (`16×6 km @ 35°`). Never claims a
 *  radius for a shape that has none. Shape fields come straight off the wire
 *  (`AreaSpec` / `RegionInfo`); nothing here re-derives geometry. */
export function areaSummary(area) {
  if (!area) return "area";
  if (area.radius_km != null) return `r${trimNumber(area.radius_km)}`;
  if (area.width_km == null || area.height_km == null) return "area";
  const angle = Number(area.angle_deg) || 0;
  const bearing = angle ? ` @ ${trimNumber(angle)}°` : "";
  return `${trimNumber(area.width_km)}×${trimNumber(area.height_km)} km${bearing}`;
}

/** Prose form of the same thing, for secondary detail lines: `radius 10 km` or
 *  `16×6 km box at 35°` — mirroring the CLI's own human wording so the App and
 *  the CLI describe the same box the same way. */
export function areaGeometry(area) {
  if (!area) return "area unknown";
  if (area.radius_km != null) return `radius ${trimNumber(area.radius_km)} km`;
  if (area.width_km == null || area.height_km == null) return "area unknown";
  const angle = Number(area.angle_deg) || 0;
  const bearing = angle ? ` at ${trimNumber(angle)}°` : "";
  return `${trimNumber(area.width_km)}×${trimNumber(area.height_km)} km box${bearing}`;
}

/** Strip grouping (any whitespace, incl. the no-break space) back to a plain
 *  numeric string. `"1 000 000" → "1000000"`; an empty/blank field → "". Does
 *  not itself coerce to Number — callers parse with parseInt/parseFloat so the
 *  int-vs-float distinction stays in config-form's `readParams`. */
export function stripGrouping(text) {
  if (text === null || text === undefined) return "";
  return String(text).replace(/\s/g, "");
}
