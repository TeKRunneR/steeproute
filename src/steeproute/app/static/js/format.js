// Shared number-format helpers (App Story app-4-2). Long numeric values (iter
// budget, stagnation iters, area cap) are shown space-grouped for readability —
// `1 000 000`, never commas (a comma is the French decimal separator, so it
// would misread). Grouping is DISPLAY-ONLY: the value on the wire / in argv
// stays a plain number. Factored out of config-form.js so the run-library
// params view (Story 4.3) groups numbers the same way.

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

/** Strip grouping (any whitespace, incl. the no-break space) back to a plain
 *  numeric string. `"1 000 000" → "1000000"`; an empty/blank field → "". Does
 *  not itself coerce to Number — callers parse with parseInt/parseFloat so the
 *  int-vs-float distinction stays in config-form's `readParams`. */
export function stripGrouping(text) {
  if (text === null || text === undefined) return "";
  return String(text).replace(/\s/g, "");
}
