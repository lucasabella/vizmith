/**
 * The spec as the wells and the drill rewrite it.
 *
 * Nothing here judges a spec. Every function returns something a person could have typed
 * into `{ } JSON`, and what decides whether it is legal is `/api/validate`, which is the
 * same judge a model's answer goes through. A second opinion in the browser is one that
 * can disagree with the one that counts.
 *
 * The renderer's `Spec` is what a validated spec looks like on the way back. A draft is
 * what is on screen while it is being built, which is the same shape with a measure that
 * may not be there yet.
 */

import type { Channel, ChannelType, Spec } from "../chart/option";

export type Mark = "bar" | "line" | "area" | "point" | "arc";

export type Join = {
  table: string;
  type?: "inner" | "left";
  on: { left: string; right: string }[];
};

export type Item = { column: string; truncate?: Unit; as?: string };
export type Aggregate = { fn: Fn; column?: string; as: string };
export type Filter = { column: string; op: string; value?: unknown };

export type Query = {
  from: string;
  joins?: Join[];
  filters?: Filter[];
  select?: Item[];
  group_by?: Item[];
  aggregates?: Aggregate[];
  order_by?: { column: string; direction?: "asc" | "desc" }[];
  limit_by?: { column: string; by: string; limit: number; direction?: "asc" | "desc" };
  limit: number;
};

export type Draft = {
  spec_version: "1";
  title?: string;
  query: Query;
  chart: { mark: Mark; stack?: boolean; encoding: { x?: Channel; y?: Channel; color?: Channel } };
};

/** A validated spec is a draft that has a measure. The renderer only ever sees one of
 * these, because it only ever draws what the API returned. */
export const drawable = (draft: Draft): draft is Draft & { chart: { encoding: { y: Channel } } } =>
  draft.chart.encoding.y !== undefined;

/** A spec off the wire, as the thing being edited. The two are one spec in two views, so
 * the JSON and the wells cannot drift apart. */
export const asDraft = (spec: Spec): Draft => spec as unknown as Draft;

const anObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

/** A list of records, or absent. What is refused is a key that is present and is something
 * else, because that is what a panel iterates and reads fields off. */
const aListOfRecords = (value: unknown): value is Record<string, unknown>[] | undefined =>
  value === undefined || (Array.isArray(value) && value.every(anObject));

const aString = (value: unknown): boolean => typeof value === "string";

/**
 * The text in `{ } JSON`, as the draft both views read, or as nothing.
 *
 * Nothing here judges whether a spec is legal. `/api/validate` is the judge and a second
 * opinion in the browser is one that can disagree with the one that counts. What this
 * answers is the question the browser has to answer before it can render anything at all:
 * whether the thing in hand is shaped like a spec. Those are different questions, and
 * answering the second one here does not put a second validator in the browser.
 *
 * It matters because editing JSON by hand means passing through states that are not a
 * spec. Every consumer reads `draft.chart.encoding`, `draft.query.filters` and the fields
 * of what those hold, without asking, and a `TypeError` thrown during render unmounts the
 * tree in React 19 — so the whole interface goes rather than the panel.
 *
 * The line this draws is what the panels walk, which is more than the top two keys. A
 * hand edit that deletes one line out of a committed fixture — `"column"` from a filter,
 * say — leaves JSON that parses, has a chart, and throws on `filter.column.split`. So the
 * lists have to be lists, and the fields a panel writes into the markup have to be text
 * where they are there at all.
 *
 * What it stops short of is contents. A mark nothing recognises, a filter with no `op`, a
 * query naming a table that does not exist: all of those reach the wells and then the
 * validator, which refuses them by name. What it costs is that a spec being typed goes
 * quiet in the wells sooner, which is what a half typed spec should do to them.
 */
export const draftIn = (text: string): Draft | null => {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return null;
  }
  if (!anObject(parsed)) return null;
  const { query, chart } = parsed;
  if (!anObject(query) || !anObject(chart) || !anObject(chart.encoding)) return null;

  const lists = ["select", "group_by", "aggregates", "filters", "order_by"];
  if (!lists.every((key) => aListOfRecords(query[key]))) return null;

  const items = [...((query.select ?? []) as never[]), ...((query.group_by ?? []) as never[])];
  const filters = (query.filters ?? []) as Record<string, unknown>[];
  return (
    // A select or group_by item is written into a well by its alias where it has one and
    // by the last segment of its column otherwise, so one of the two has to be text.
    items.every((item: Record<string, unknown>) => aString(item.column) || aString(item.as)) &&
    // A filter chip splits the column and rewrites the operator, so both of those do.
    filters.every((filter) => aString(filter.column) && aString(filter.op))
      ? (parsed as unknown as Draft)
      : null
  );
};

export const WELLS = ["Axis", "Legend", "Values", "Top N", "Filters"] as const;
export type Well = (typeof WELLS)[number];

export const FNS = ["sum", "avg", "min", "max", "count", "count_distinct"] as const;
export type Fn = (typeof FNS)[number];

export const UNITS = ["year", "quarter", "month", "week", "day", "hour"] as const;
export type Unit = (typeof UNITS)[number];

/** The column types a profile reports, which is what a dragged column carries. */
export type Field = { table: string; column: string; type: string };

const NUMERIC = ["integer", "decimal"];
const TEMPORAL = ["date", "timestamp"];

/** Where a spec starts when the first column lands in an empty panel. The cap is the one
 * the fixtures use; the mark is a separate control and a bar is what it starts on. */
export const ROW_CAP = 500;
export const TOP_N = 10;

/**
 * What a well infers, which is the decision this feature had to make. A default rather
 * than a question at the moment of the drop, because a dialogue on every drag makes
 * dragging slower than typing. It is shown in the well and can be changed there, which is
 * what keeps it from being the quiet kind of wrong: an aggregate nobody chose and nobody
 * can see is the failure this project exists to avoid.
 */
export const aggregateFor = (type: string): Fn => (NUMERIC.includes(type) ? "sum" : "count");

/** A date on an axis is drawn per month unless it says otherwise. Every row of its own is
 * a chart of a thousand marks, and a year is four bars. */
export const truncateFor = (type: string): Unit | undefined =>
  TEMPORAL.includes(type) ? "month" : undefined;

/** What a channel calls a column's type. A truncated date stays temporal: it is still a
 * point in time, just a coarser one. */
export const channelType = (type: string): ChannelType =>
  NUMERIC.includes(type) ? "quantitative" : TEMPORAL.includes(type) ? "temporal" : "nominal";

export const table = (name: string): string => name.split(".").slice(-1)[0];

/**
 * Whether a reference in a spec names a table. Any trailing part counts, so `orders` and
 * `shop.orders` both name `warehouse.shop.orders`. The same rule the validator applies,
 * because a reference these two disagree about is a spec that looks right here and is
 * refused there.
 */
export function namesTable(name: string, reference: string): boolean {
  const segments = name.split(".");
  const wanted = reference.split(".");
  return (
    wanted.length <= segments.length &&
    segments.slice(segments.length - wanted.length).join(".") === wanted.join(".")
  );
}

/**
 * How to write this column in a spec: qualified with the name the query already uses for
 * its table, and with the source's full name where the query does not read it yet.
 *
 * A profile always names a table on every level and a spec often names it on one, so
 * writing the full name into a query that says `orders` produces a qualifier the
 * validator cannot resolve. The query's own word for the table is the one that resolves.
 */
export function qualified(field: Field, query?: Query): string {
  const reference =
    query === undefined
      ? field.table
      : (tablesIn(query).find((name) => namesTable(field.table, name)) ?? field.table);
  return `${reference}.${field.column}`;
}

/** Every name the query produces, in the builder's order. The same rule as
 * `output_columns` in the validator, which is what these names are checked against. */
export function outputColumns(query: Query): string[] {
  const items = [...(query.select ?? []), ...(query.group_by ?? [])];
  return [
    ...items.map((item) => item.as ?? item.column.split(".").slice(-1)[0]),
    ...(query.aggregates ?? []).map((aggregate) => aggregate.as),
  ];
}

/**
 * What this column will be called in the result set. Its own name, unless the query
 * already produces one by that name, in which case the table it came from goes in front.
 * Two columns called `id` would otherwise be an output column produced twice, which the
 * validator rejects and which nobody dragging a field meant to ask for.
 */
export function aliasFor(field: Field, taken: string[]): string {
  if (!taken.includes(field.column)) return field.column;
  const prefixed = `${table(field.table)}_${field.column}`.slice(0, 63);
  if (!taken.includes(prefixed)) return prefixed;
  for (let n = 2; ; n += 1) {
    const numbered = `${prefixed}_${n}`.slice(0, 63);
    if (!taken.includes(numbered)) return numbered;
  }
}

const EMPTY: Draft = {
  spec_version: "1",
  query: { from: "", limit: ROW_CAP },
  chart: { mark: "bar", encoding: {} },
};

/** The tables a query reads, which is what says whether a dropped column needs a join. */
export const tablesIn = (query: Query): string[] => [
  query.from,
  ...(query.joins ?? []).map((join) => join.table),
];

/** Whether a column can be dropped without resolving anything. A qualifier matches any
 * trailing part of a name, the same way the validator resolves one. */
export const inQuery = (query: Query, field: Field): boolean =>
  tablesIn(query).some(
    (name) => namesTable(field.table, name) || namesTable(name, field.table),
  );

/**
 * A column into a well, as the spec that produces. `joins` is the path a resolver
 * returned for a column on another table, and is empty for one the query already reads.
 * Nothing here decides whether a join is legal: a caller with no path does not call this.
 */
export function place(draft: Draft | null, well: Well, field: Field, joins: Join[] = []): Draft {
  const base = draft ?? { ...EMPTY, query: { ...EMPTY.query, from: field.table } };
  const query = withJoins(base.query, joins);
  const spec: Draft = { ...base, query };

  if (well === "Filters") return filtered(spec, field);
  if (well === "Top N") return ranked(spec, field);
  if (well === "Values") return measured(spec, field);
  return grouped(spec, field, well === "Axis" ? "x" : "color");
}

/** A well emptied. The way back from a drop, since a drop with no way back is a trap. */
export function clear(draft: Draft, well: Well, index = 0): Draft {
  if (well === "Filters") {
    const filters = (draft.query.filters ?? []).filter((_, at) => at !== index);
    return { ...draft, query: pruned({ ...draft.query, filters }) };
  }
  if (well === "Top N") {
    const { limit_by: _dropped, ...query } = draft.query;
    return { ...draft, query };
  }
  const channel = well === "Values" ? "y" : well === "Axis" ? "x" : "color";
  const bound = draft.chart.encoding[channel];
  if (bound === undefined) return draft;
  return unbind({ ...draft }, bound.field);
}

/** The aggregate a measure is taken with, changed after the fact. The well shows it, so
 * this is what the person changes it through. */
export function reaggregate(draft: Draft, fn: Fn): Draft {
  const measure = draft.chart.encoding.y?.field;
  if (measure === undefined) return draft;
  const aggregates = (draft.query.aggregates ?? []).map((aggregate) =>
    aggregate.as === measure ? { ...aggregate, fn } : aggregate,
  );
  return { ...draft, query: { ...draft.query, aggregates } };
}

/** The unit a date axis is truncated to, or none for a row per value. */
export function retruncate(draft: Draft, channel: "x" | "color", unit: Unit | null): Draft {
  const field = draft.chart.encoding[channel]?.field;
  if (field === undefined) return draft;
  const group_by = (draft.query.group_by ?? []).map((item) => {
    if ((item.as ?? item.column.split(".").slice(-1)[0]) !== field) return item;
    const { truncate: _was, ...rest } = item;
    return unit === null ? rest : { ...rest, truncate: unit };
  });
  return { ...draft, query: { ...draft.query, group_by } };
}

/** The number of values a Top N keeps. */
export function retop(draft: Draft, limit: number): Draft {
  const limit_by = draft.query.limit_by;
  if (limit_by === undefined) return draft;
  return { ...draft, query: { ...draft.query, limit_by: { ...limit_by, limit } } };
}

/**
 * Whether this chart is one `limit` would cut through the middle of. A row cap on a multi
 * series query drops part of a series and draws the rest as if it were the data, so
 * `limit_by` is not optional there. The interface says `Required` for the same reason the
 * validator refuses it.
 */
export const ranksRequired = (draft: Draft): boolean => draft.chart.encoding.color !== undefined;

function grouped(draft: Draft, field: Field, channel: "x" | "color"): Draft {
  const bound = draft.chart.encoding[channel];
  const cleared = bound === undefined ? draft : unbind(draft, bound.field);
  const alias = aliasFor(field, outputColumns(cleared.query));
  const item: Item = { column: qualified(field, cleared.query), as: alias };
  const unit = truncateFor(field.type);
  if (unit !== undefined) item.truncate = unit;

  return {
    ...cleared,
    query: { ...cleared.query, group_by: [...(cleared.query.group_by ?? []), item] },
    chart: {
      ...cleared.chart,
      encoding: {
        ...cleared.chart.encoding,
        [channel]: { field: alias, type: channelType(field.type) },
      },
    },
  };
}

function measured(draft: Draft, field: Field): Draft {
  const bound = draft.chart.encoding.y;
  const cleared = bound === undefined ? draft : unbind(draft, bound.field);
  const alias = aliasFor(field, outputColumns(cleared.query));
  const aggregate: Aggregate = {
    fn: aggregateFor(field.type),
    column: qualified(field, cleared.query),
    as: alias,
  };

  return {
    ...cleared,
    query: { ...cleared.query, aggregates: [...(cleared.query.aggregates ?? []), aggregate] },
    chart: {
      ...cleared.chart,
      encoding: { ...cleared.chart.encoding, y: { field: alias, type: "quantitative" } },
    },
  };
}

/**
 * A column into Top N. It ranks an output column the query already groups by, so a column
 * the chart does not group by is refused rather than quietly added as a dimension: adding
 * one changes what every bar counts, which is not what dropping a field into a row cap
 * asked for.
 */
function ranked(draft: Draft, field: Field): Draft {
  const outputs = outputColumns(draft.query);
  const dimension = (draft.query.group_by ?? []).find(
    (item) => item.column === qualified(field, draft.query) || item.column.endsWith(`.${field.column}`),
  );
  const measure = draft.chart.encoding.y?.field;
  if (dimension === undefined || measure === undefined) {
    throw new WellRefusal(
      dimension === undefined
        ? `Top N ranks a column the chart already groups by. Put ${field.column} on Axis or Legend first.`
        : "Top N ranks by a measure, so put a column in Values first.",
    );
  }
  const column = dimension.as ?? dimension.column.split(".").slice(-1)[0];
  if (!outputs.includes(column)) throw new WellRefusal(`'${column}' is not an output column`);

  return {
    ...draft,
    query: { ...draft.query, limit_by: { column, by: measure, limit: TOP_N, direction: "desc" } },
  };
}

/**
 * A column into Filters. It filters out the rows where that column is null, which is the
 * one filter that needs no operator and no value, and choosing either of those is its own
 * screen rather than something a drag guesses at.
 */
function filtered(draft: Draft, field: Field): Draft {
  const filter: Filter = { column: qualified(field, draft.query), op: "is_not_null" };
  return { ...draft, query: { ...draft.query, filters: [...(draft.query.filters ?? []), filter] } };
}

/** A refusal a well produces on its own, before anything is sent. It is not a validation
 * verdict: it says the drop does not describe a spec at all. */
export class WellRefusal extends Error {}

function withJoins(query: Query, joins: Join[]): Query {
  const known = tablesIn(query);
  const added = joins.filter((join) => !known.includes(join.table));
  if (added.length === 0) return query;
  return { ...query, joins: [...(query.joins ?? []), ...added] };
}

/**
 * One output column taken out of the query, and everything that pointed at it with it. A
 * dangling `order_by` or `limit_by` is a spec the validator refuses for a reason the
 * person did not cause and cannot see in the wells.
 */
function unbind(draft: Draft, alias: string): Draft {
  const query = draft.query;
  const group_by = (query.group_by ?? []).filter(
    (item) => (item.as ?? item.column.split(".").slice(-1)[0]) !== alias,
  );
  const aggregates = (query.aggregates ?? []).filter((aggregate) => aggregate.as !== alias);
  const encoding = { ...draft.chart.encoding };
  for (const channel of ["x", "y", "color"] as const) {
    if (encoding[channel]?.field === alias) delete encoding[channel];
  }
  return {
    ...draft,
    query: pruned({ ...query, group_by, aggregates }),
    chart: { ...draft.chart, encoding },
  };
}

/** A query with the parts that are now empty or dangling taken out, so the JSON reads as
 * a spec somebody wrote rather than as the wreckage of one. */
function pruned(query: Query): Query {
  const out: Query = { ...query };
  const outputs = outputColumns(out);

  if (out.group_by?.length === 0) delete out.group_by;
  if (out.aggregates?.length === 0) delete out.aggregates;
  if (out.filters?.length === 0) delete out.filters;

  const order_by = (out.order_by ?? []).filter((order) => outputs.includes(order.column));
  if (order_by.length > 0) out.order_by = order_by;
  else delete out.order_by;

  // 'by' is held to the aggregate aliases rather than to the output columns, because that is
  // the rule the validator applies: ranking a dimension has no measure to rank it by.
  const measures = (out.aggregates ?? []).map((aggregate) => aggregate.as);
  if (out.limit_by && !(outputs.includes(out.limit_by.column) && measures.includes(out.limit_by.by))) {
    delete out.limit_by;
  }
  return out;
}
