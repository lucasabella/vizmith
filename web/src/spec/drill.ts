/**
 * Clicking a mark asks the same question again about the thing that was clicked.
 *
 * The filter half is mechanical: the value came out of the result set and goes back in as
 * a bound parameter, and the validator sees the new spec before anything runs. The other
 * half is the decision this feature had to make. A filter on its own gives a chart of one
 * bar, so the narrowed question has to be grouped by something else, and nothing in the
 * product knows what that is.
 *
 * It asks the person, from the dimensions of the tables the query already reads. The
 * alternatives were the model, which makes a click slow and unrepeatable, and reading it
 * off the profiles, which is deterministic and occasionally baffling. A menu costs one
 * click and is the only one of the three where what happens next is visible before it
 * happens. See DESIGN.md.
 */

import type { Row, Value } from "../chart/option";
import {
  type Condition,
  type Draft,
  type Field,
  type Item,
  type Spec,
  aliasFor,
  channelType,
  inQuery,
  outputColumns,
  qualified,
  truncateFor,
} from "./spec";

/** Why a click produced no question. It is shown where the menu would have been, because
 * a click that does nothing and says nothing is indistinguishable from a broken chart. */
export class NoDrill extends Error {}

/** What was clicked: the category on the axis, and the series when a colour channel made
 * one. Both are the labels the renderer drew, which is what ECharts hands back. */
export type Clicked = { category: Value; series?: string };

/**
 * The dimensions a narrowed question could be grouped by: every non measure column of a
 * table the query already reads, minus the one that was drilled into, which is now a
 * filter and would draw one bar again.
 *
 * Nothing is offered from a table the query does not read. That would need a join path,
 * and a join a click made is a join nobody confirmed.
 */
export function candidates(draft: Draft, columns: Field[]): Field[] {
  const drilled = itemFor(draft, "x")?.column;
  return columns.filter(
    (field) =>
      inQuery(draft.query, field) &&
      channelType(field.type) !== "quantitative" &&
      qualified(field, draft.query) !== drilled,
  );
}

/**
 * The narrowed question. The clicked category becomes a filter on the column that was
 * bound to the axis, and `by` takes its place as the dimension.
 *
 * A null category is filtered with `is_null` rather than compared to anything, because a
 * null is not a value and `= ''` is a different question with an answer that looks just
 * as plausible. A truncated date axis is refused: the label is a month and the column
 * behind it holds days, so `= '2026-01-01'` would silently mean the first of the month.
 */
export function drill(spec: Spec, rows: Row[], clicked: Clicked, by: Field): Spec {
  const item = itemFor(spec, "x");
  if (item === undefined) throw new NoDrill("This chart has no dimension to drill into.");
  if (item.truncate !== undefined) {
    throw new NoDrill(
      `The axis is one ${item.truncate} per mark, and a filter on '${short(item.column)}' would ` +
        "mean one of them rather than all of it. Drilling a truncated date is not supported.",
    );
  }
  if (qualified(by, spec.query) === item.column) {
    throw new NoDrill(`'${by.column}' is the column that was clicked, so it would draw one mark.`);
  }

  const value = valueOf(spec, rows, clicked);
  const filter: Condition =
    value === null
      ? { column: item.column, op: "is_null" }
      : { column: item.column, op: "=", value };

  const alias = item.as ?? short(item.column);
  const group_by = (spec.query.group_by ?? []).filter(
    (each) => (each.as ?? short(each.column)) !== alias,
  );
  const renamed = aliasFor(by, outputColumns({ ...spec.query, group_by }));
  const replacement: Item = { column: qualified(by, spec.query), as: renamed };
  // The same unit a well would have inferred for this column. A date grouped by every
  // value it holds is a chart of a thousand marks, and a drill that produces one has
  // answered the question in a way nobody can read.
  const unit = truncateFor(by.type);
  if (unit !== undefined) replacement.truncate = unit;

  const query = {
    ...spec.query,
    filters: [...(spec.query.filters ?? []), filter],
    group_by: [...group_by, replacement],
  };

  return {
    ...spec,
    title: title(spec, by, clicked),
    query: retargeted(query, alias, renamed),
    chart: {
      ...spec.chart,
      encoding: {
        ...spec.chart.encoding,
        x: { field: renamed, type: channelType(by.type) },
      },
    },
  };
}

/**
 * The value behind the clicked mark, taken out of the result set rather than off the
 * label. A label is a string the renderer wrote, and a number filtered as its own text
 * would compare a category against a total. The row is found by the label because that is
 * what identifies a mark, and its value is the one the source sent.
 */
function valueOf(spec: Spec, rows: Row[], clicked: Clicked): Value {
  const { x, color } = spec.chart.encoding;
  if (x === undefined) throw new NoDrill("This chart has no dimension to drill into.");
  const shown = (value: Value) => (value === null ? null : String(value));
  const match = rows.find(
    (row) =>
      shown(row[x.field]) === shown(clicked.category) &&
      (color === undefined || clicked.series === undefined || shown(row[color.field]) === clicked.series),
  );
  if (match === undefined) {
    // A label the result set no longer holds. Filtering on the label's own text would be
    // filtering on something the renderer produced rather than on something the source
    // sent, which is how a formatted number becomes a filter nobody meant.
    throw new NoDrill("The clicked mark is not in the result set on screen any more.");
  }
  return match[x.field];
}

/** `order_by` and `limit_by` follow the column that replaced the drilled one, so a chart
 * that ranked its axis still ranks the axis it now has. */
function retargeted(query: Draft["query"], was: string, now: string) {
  const order_by = (query.order_by ?? []).map((order) =>
    order.column === was ? { ...order, column: now } : order,
  );
  const limit_by =
    query.limit_by && query.limit_by.column === was
      ? { ...query.limit_by, column: now }
      : query.limit_by;
  return {
    ...query,
    ...(order_by.length > 0 ? { order_by } : {}),
    ...(limit_by ? { limit_by } : {}),
  };
}

function itemFor(draft: Draft, channel: "x" | "color"): Item | undefined {
  const field = draft.chart.encoding[channel]?.field;
  if (field === undefined) return undefined;
  return (draft.query.group_by ?? []).find((item) => (item.as ?? short(item.column)) === field);
}

/** What the narrowed chart is called. The clicked value is in it, because a chart whose
 * title still says "revenue per country" after a drill into one country is a chart that
 * lies about what it shows. */
function title(draft: Draft, by: Field, clicked: Clicked): string {
  const measure = draft.chart.encoding.y?.field ?? "value";
  const category = clicked.category === null ? "(no value)" : String(clicked.category);
  return `${measure} per ${by.column} in ${category}`.slice(0, 200);
}

const short = (column: string): string => column.split(".").slice(-1)[0];
