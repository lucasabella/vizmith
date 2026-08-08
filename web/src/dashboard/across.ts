/**
 * One filter, applied to every tile it can reach.
 *
 * A dashboard was up to 24 tiles with nothing crossing between them, which is a coherent
 * place to have stopped and is also the first thing anybody asks for after saving their
 * second one: the whole reason to put several charts on a page is that they are about the
 * same thing. What crosses is a filter, and it is a spec rewrite applied when a tile runs
 * rather than a new kind of stored state — the same move `drill.ts` makes, one level up.
 *
 * Nothing here is written into a tile's spec. A tile holds the question somebody built, and
 * a narrowing of the whole page has to come off again without leaving a trace in a spec
 * nobody edited. So the dashboard holds the filters, each tile is handed the spec it would
 * run *with them applied*, and the result goes through `/api/execute` and its validator like
 * every other spec in this product. Nothing here judges one.
 *
 * The decision the feature had to make is what happens to a tile the filter cannot reach —
 * a tile whose query never reads the filtered column. Three answers, and two of them are
 * wrong. Guessing a join is the worst: a join nobody confirmed is the failure this whole
 * design exists to prevent, and it would produce a plausible number rather than an error.
 * Dropping the tile is the second: a dashboard that hides half of itself when a filter is
 * added is one nobody can read. So the tile draws what it always drew and *says* it was not
 * narrowed, which is the only one of the three where what happened is visible. See
 * DESIGN.md.
 */

import {
  anyOf,
  inQuery,
  namesTable,
  qualified,
  type Condition,
  type Field,
  type Filter,
  type Spec,
} from "../spec/spec";

/** The filters a dashboard applies to every tile it can. A list, so they are a conjunction
 * — the same reading `query.filters` has, for the same reason: two narrowings of one page
 * mean both, and a person who wanted either wrote one filter with an `any` in it. */
export type Across = Filter[];

/** A tile's spec with the dashboard's filters in it, and the ones that did not fit.
 * `missed` is not an error: it is what the tile says about itself. */
export type Narrowed = { spec: Spec; missed: Filter[] };

/** Every condition a filter holds: itself, or the ones under its `any`. The same flattening
 * `conditions` does in the validator, and for the same callers — everything that asks a
 * question about the columns a filter reads wants the flat list. */
export const conditionsOf = (filter: Filter): Condition[] => (anyOf(filter) ? filter.any : [filter]);

/**
 * The column of a condition, split the way the grammar splits one: the last segment is the
 * column and everything before it names the table. A condition with nothing before it names
 * no table, which the store refuses and this reports as reaching nothing.
 */
export const fieldOf = (column: string): Field => {
  const at = column.lastIndexOf(".");
  return {
    table: at === -1 ? "" : column.slice(0, at),
    column: column.slice(at + 1),
    // A dashboard filter carries no type. Nothing downstream reads one — the value was
    // typed against the column's type where it was written, and the source compares them.
    type: "",
  };
};

/** Whether a tile's query reads the table a column names. `inQuery` is the wells' own rule
 * and the validator's, so a filter this says reaches a tile is one whose rewritten column
 * that tile's query can resolve. */
export const reaches = (spec: Spec, column: string): boolean => {
  const field = fieldOf(column);
  return field.table !== "" && inQuery(spec.query, field);
};

/** Whether a whole filter reaches a tile. Every condition of a disjunction has to: an `OR`
 * with one side dropped is a wider question than the one that was written, and a wider
 * filter silently applied is worse than one that says it did not apply at all. */
const fits = (spec: Spec, filter: Filter): boolean =>
  conditionsOf(filter).every((condition) => reaches(spec, condition.column));

/** A filter with each column written the way this query names its tables. */
const written = (spec: Spec, filter: Filter): Filter => {
  const rewrite = (condition: Condition): Condition => ({
    ...condition,
    column: qualified(fieldOf(condition.column), spec.query),
  });
  return anyOf(filter) ? { any: filter.any.map(rewrite) } : rewrite(filter);
};

/**
 * One tile's spec, narrowed by the filters that reach it.
 *
 * The column is rewritten to the query's own word for the table before it goes in. A
 * dashboard filter names its table in full, a query often names it in one segment, and the
 * validator resolves a reference against the tables the query actually lists — so
 * `vizmith.shop.orders.status` dropped unchanged into a query that says `shop.orders`
 * refers to a table that query does not have. `qualified` is the same function the wells
 * use to write a dragged column, for the same reason.
 *
 * The spec comes back by identity where nothing applied. A tile fetches on the spec it was
 * given, so a fresh object per render would be a fetch per render.
 */
export function narrowed(spec: Spec, across: Across): Narrowed {
  const applied = across.filter((filter) => fits(spec, filter));
  const missed = across.filter((filter) => !fits(spec, filter));
  if (applied.length === 0) return { spec, missed };
  return {
    spec: {
      ...spec,
      query: {
        ...spec.query,
        filters: [...(spec.query.filters ?? []), ...applied.map((filter) => written(spec, filter))],
      },
    },
    missed,
  };
}

/** How many tiles a filter reaches, which is what the chip says. A filter that reaches none
 * of them does nothing, and a person who added one should not have to read every tile to
 * find that out. */
export const reach = (tiles: { spec: Spec }[], filter: Filter): number =>
  tiles.filter((tile) => fits(tile.spec, filter)).length;

/**
 * The columns a dashboard filter could be about: every dimension its tiles group by, named
 * with the table it belongs to.
 *
 * From the tiles rather than from the schema, and that is the argument. A dashboard is
 * about the thing its charts are about, so the columns worth crossing them with are the
 * ones they are already grouped by; offering every column of every table would be a menu of
 * a hundred and fifty things, most of which reach no tile. A dimension a query wrote
 * unqualified is qualified here with that query's `from`, because the list is matched
 * against every tile and not only the one it came from.
 *
 * Measures are not here. A condition on a measure is `having`, it runs after the grouping,
 * and applying one across a dashboard would mean something different in every tile.
 */
export function dimensions(tiles: { spec: Spec }[]): string[] {
  const found = new Set<string>();
  for (const tile of tiles) {
    for (const item of tile.spec.query.group_by ?? []) {
      if (item.column === undefined) continue;
      found.add(item.column.includes(".") ? item.column : `${tile.spec.query.from}.${item.column}`);
    }
  }
  return [...found].sort();
}

/**
 * The type the schema reports for a column named in a spec, or "" where nothing knows.
 *
 * The bar needs it to decide what a value may be: a date column is where "the start of this
 * month" is worth offering and a number is where a typed value has to be parsed as one
 * rather than sent as the string somebody typed. Matched in both directions, because a spec
 * names a table with as many segments as it likes and a profile always names it with all of
 * them. Empty is a real answer and the bar treats it as "a value I type", which is what
 * every column got before this existed.
 */
export function typeOf(column: string, columns: Field[]): string {
  const want = fieldOf(column);
  const found = columns.find(
    (field) =>
      field.column === want.column &&
      (namesTable(field.table, want.table) || namesTable(want.table, field.table)),
  );
  return found?.type ?? "";
}

/** What a relative value says, in words. The grammar's tokens are a closed set, so this is
 * a lookup rather than a formatter. */
function said(value: unknown): string {
  if (value === null) return "null";
  if (typeof value !== "object") return String(value);
  const relative = value as { relative?: string; unit?: string; count?: number };
  switch (relative.relative) {
    case "now":
      return "now";
    case "today":
      return "today";
    case "start_of":
      return `the start of this ${relative.unit}`;
    case "ago":
      return `${relative.count} ${relative.unit}${(relative.count ?? 0) > 1 ? "s" : ""} ago`;
    default:
      return JSON.stringify(value);
  }
}

/** What each operator reads as on a chip. The symbols are the grammar's own and are left
 * alone; the ones that are words are written as words, because "status is_null" is not a
 * sentence and the chip is the only place a person reads the filter back. */
const READS: Record<Condition["op"], string> = {
  "=": "=",
  "!=": "≠",
  "<": "<",
  "<=": "≤",
  ">": ">",
  ">=": "≥",
  in: "is one of",
  not_in: "is none of",
  is_null: "is null",
  is_not_null: "is not null",
};

const one = (condition: Condition): string => {
  const column = condition.column.split(".").slice(-1)[0];
  const reads = READS[condition.op];
  if (condition.op === "is_null" || condition.op === "is_not_null") return `${column} ${reads}`;
  const value = Array.isArray(condition.value)
    ? condition.value.map(said).join(", ")
    : said(condition.value);
  return `${column} ${reads} ${value}`;
};

/** A filter, as the chip says it. The column keeps its last segment only: the table is what
 * decides which tiles it reaches and is in the chip's title, and a chip four segments wide
 * is one that pushes the next one off the row. */
export const describe = (filter: Filter): string =>
  conditionsOf(filter).map(one).join(" or ");
