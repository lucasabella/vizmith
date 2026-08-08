import type { Row, Value } from "./option";
import type { Spec } from "../spec/spec";

/**
 * Three ways a chart leaves the tab that drew it: the spec, the rows, the picture.
 *
 * The spec first, because it is the artefact this whole project is built around — versioned,
 * validated, diffable — and until now there was no control that handed somebody a copy of
 * it. The rows next, because they are already in the browser in the builder's own column
 * order and a file of them costs no request. The picture last, because it is the one a
 * screenshot tool could already produce.
 *
 * Everything here is pure but the two functions that touch the document, which are at the
 * bottom and are the only reason this file knows what a browser is.
 */

/** What a spreadsheet does with a cell that starts like this: runs it. */
const FORMULA = /^[=+\-@\t\r]/;

/** Plain numbers, which start with `-` and are not formulas. */
const NUMERIC = /^-?\d+(\.\d+)?([eE][+-]?\d+)?$/;

/**
 * One value as a CSV field.
 *
 * Two escapes, and the second one is the one that gets forgotten. RFC 4180 says a field
 * holding a quote, a comma or a newline is wrapped in quotes with its own quotes doubled,
 * which is why this is a writer rather than a join on commas: a category called
 * `Pencils, HB` would otherwise become two columns and every row after it would be wrong.
 *
 * The second is that a spreadsheet treats a cell beginning `=`, `+`, `-` or `@` as a
 * formula and evaluates it on open, so a value out of somebody's warehouse becomes code in
 * their spreadsheet. This is the same rule as the tooltip drawn in `richText` — a row value
 * never becomes markup, and it never becomes a formula either — and the fix is the usual
 * one: a leading apostrophe, which spreadsheets read as "this is text". A number is left
 * alone, because `-5` is a number and prefixing it would put text in a numeric column.
 */
export function field(value: Value): string {
  if (value === null) return "";
  if (typeof value !== "string") return String(value);
  const guarded = FORMULA.test(value) && !NUMERIC.test(value) ? `'${value}` : value;
  return /["\n\r,]/.test(guarded) ? `"${guarded.replace(/"/g, '""')}"` : guarded;
}

/**
 * The rows as a CSV, in the result set's own column order.
 *
 * The order is read off the first row rather than off the spec, for the same reason the
 * Table tab reads it there: the spec's encoding names three of the columns and the query
 * may produce more. CRLF because that is what RFC 4180 says and what the spreadsheet that
 * still cares about it wants; everything else has read both for twenty years.
 */
export function csv(rows: Row[]): string {
  if (rows.length === 0) return "";
  const columns = Object.keys(rows[0]);
  const lines = [columns.map(field).join(",")];
  for (const row of rows) lines.push(columns.map((column) => field(row[column])).join(","));
  return lines.join("\r\n") + "\r\n";
}

/**
 * A file name from a chart's title, or from what it is of when it has none.
 *
 * A title comes from a model or from a person, so it can hold anything a string can hold,
 * including a slash and a null byte. What is left is letters, digits and single hyphens,
 * which is a name every filesystem takes and no shell has an opinion about.
 */
export function fileName(spec: Spec, extension: string): string {
  const said = spec.title ?? spec.chart.encoding.y.field;
  const safe = said
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60);
  return `${safe || "chart"}.${extension}`;
}

/**
 * Put something on the clipboard, and say whether it went.
 *
 * `navigator.clipboard` is absent on an insecure origin and can be refused by permission,
 * and both arrive as a rejected promise or as nothing at all. A control that says "Copied"
 * when nothing was copied is worse than one that says it could not, so the caller is told
 * which happened.
 */
export async function copy(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

/**
 * Hand a file to the browser.
 *
 * An anchor with a `download` attribute, clicked and thrown away, which is the one thing
 * every browser does the same. The object URL is revoked afterwards: it holds the blob
 * alive for as long as the document does, and a person exporting a dashboard's worth of
 * rows would otherwise keep every one of them.
 */
export function download(name: string, blob: Blob): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  URL.revokeObjectURL(url);
}
