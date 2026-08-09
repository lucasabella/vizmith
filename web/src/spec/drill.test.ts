import { describe, expect, it } from "vitest";
import type { Row } from "../chart/option";
import type { Spec } from "./spec";
import { NoDrill, candidates, drill } from "./drill";
import { type Condition, type Draft, type Field, anyOf } from "./spec";

/** The one condition a drill wrote. A drill narrows to a single clicked value, so it never
 * writes a disjunction; this says so to the checker and fails loudly if that stops holding. */
function only(spec: Spec): Condition {
  const [filter] = spec.query.filters ?? [];
  if (filter === undefined || anyOf(filter)) throw new Error(`not one condition: ${JSON.stringify(filter)}`);
  return filter;
}

const field = (table: string, column: string, type = "string"): Field => ({ table, column, type });

const CATEGORY = field("vizmith.shop.products", "category");
const STATUS = field("vizmith.shop.orders", "status");
const COUNTRY = field("vizmith.shop.customers", "country");
const ELSEWHERE = field("vizmith.shop.carriers", "name");

/** The chart a drill starts from: revenue per country, over two tables. */
const chart = (truncate?: string): Draft => ({
  spec_version: "1",
  title: "Revenue per country",
  query: {
    from: "vizmith.shop.customers",
    joins: [
      {
        table: "vizmith.shop.orders",
        on: [{ left: "vizmith.shop.orders.customer_id", right: "vizmith.shop.customers.id" }],
      },
    ],
    group_by: [
      truncate === undefined
        ? { column: "vizmith.shop.customers.country", as: "country" }
        : { column: "vizmith.shop.orders.order_date", as: "country", truncate: "month" },
    ],
    aggregates: [{ fn: "sum", column: "vizmith.shop.orders.total", as: "revenue" }],
    order_by: [{ column: "revenue", direction: "desc" }],
    limit: 500,
  },
  chart: {
    mark: "bar",
    encoding: {
      x: { field: "country", type: "nominal" },
      y: { field: "revenue", type: "quantitative" },
    },
  },
});

const spec = (draft: Draft) => draft as unknown as Spec;

const rows: Row[] = [
  { country: "Netherlands", revenue: 91240.5 },
  { country: "Germany", revenue: 70112 },
  { country: null, revenue: 12 },
];

describe("what the narrowed question can be grouped by", () => {
  const columns = [CATEGORY, STATUS, COUNTRY, ELSEWHERE, field("vizmith.shop.orders", "total", "decimal")];

  it("offers the dimensions of the tables the query already reads", () => {
    const offered = candidates(chart(), columns).map((each) => each.column);

    expect(offered).toEqual(["status"]);
  });

  it("offers nothing from a table the query does not read, since a click cannot join", () => {
    expect(candidates(chart(), [ELSEWHERE])).toEqual([]);
  });

  it("offers no measure, because a chart grouped by a total is not a question", () => {
    const measures = candidates(chart(), columns).filter((each) => each.type === "decimal");

    expect(measures).toEqual([]);
  });
});

describe("clicking a mark", () => {
  it("filters on the clicked value and groups by what was chosen", () => {
    const narrowed = drill(spec(chart()), rows, { category: "Netherlands" }, STATUS);

    expect(narrowed.query.filters).toEqual([
      { column: "vizmith.shop.customers.country", op: "=", value: "Netherlands" },
    ]);
    expect(narrowed.query.group_by).toEqual([{ column: "vizmith.shop.orders.status", as: "status" }]);
    expect(narrowed.chart.encoding.x).toEqual({ field: "status", type: "nominal" });
  });

  it("takes the value out of the result set rather than off the label", () => {
    const numbers: Row[] = [{ country: 4, revenue: 10 }];
    const narrowed = drill(spec(chart()), numbers, { category: "4" }, STATUS);

    expect(only(narrowed).value).toBe(4);
  });

  it("keeps the value out of the SQL by leaving it a value, quote and all", () => {
    const quoted: Row[] = [{ country: "O'Hare's", revenue: 1 }];
    const narrowed = drill(spec(chart()), quoted, { category: "O'Hare's" }, STATUS);

    expect(only(narrowed).value).toBe("O'Hare's");
  });

  it("drills a null with is_null rather than comparing it to an empty string", () => {
    const narrowed = drill(spec(chart()), rows, { category: null }, STATUS);

    expect(narrowed.query.filters).toEqual([{ column: "vizmith.shop.customers.country", op: "is_null" }]);
  });

  it("moves an order_by onto the column that replaced the drilled one", () => {
    const ranked = chart();
    ranked.query.order_by = [{ column: "country", direction: "asc" }];

    const narrowed = drill(spec(ranked), rows, { category: "Germany" }, STATUS);

    expect(narrowed.query.order_by).toEqual([{ column: "status", direction: "asc" }]);
  });

  it("moves a window onto it as well, so a running total is still read along the axis", () => {
    // A window names output columns, and the drilled one is about to stop being one. Left
    // alone it names a column the query no longer has, which the validator refuses for
    // something the person did not do: they clicked a bar.
    const accumulating = chart();
    accumulating.query.windows = [
      { fn: "running_total", of: "revenue", along: "country", as: "revenue_so_far" },
      { fn: "rank", of: "revenue", partition_by: ["country"], as: "place" },
    ];

    const narrowed = drill(spec(accumulating), rows, { category: "Germany" }, STATUS);

    expect(narrowed.query.windows).toEqual([
      { fn: "running_total", of: "revenue", along: "status", as: "revenue_so_far" },
      { fn: "rank", of: "revenue", partition_by: ["status"], as: "place" },
    ]);
  });

  it("truncates a date it groups by, the way a well would have", () => {
    const monthly = drill(spec(chart()), rows, { category: "Germany" }, {
      table: "vizmith.shop.customers",
      column: "signup_date",
      type: "date",
    });

    expect(monthly.query.group_by?.[0]).toMatchObject({ truncate: "month" });
    expect(monthly.chart.encoding.x?.type).toBe("temporal");
  });

  it("says what it now shows, since the old title would be a lie", () => {
    const narrowed = drill(spec(chart()), rows, { category: "Germany" }, STATUS);

    expect(narrowed.title).toContain("Germany");
    expect(narrowed.title).toContain("status");
  });

  it("refuses to group by the column that was clicked", () => {
    expect(() => drill(spec(chart()), rows, { category: "Germany" }, COUNTRY)).toThrow(NoDrill);
  });

  it("refuses a truncated date axis, where a filter would mean one day of a month", () => {
    expect(() => drill(spec(chart("month")), [{ country: "2026-01-01", revenue: 1 }], { category: "2026-01-01" }, STATUS)).toThrow(
      /truncated date/,
    );
  });

  it("refuses a computed axis, where there is no column behind the label", () => {
    // The narrowed question would have to filter on the result of the arithmetic, which
    // the grammar cannot say — and which is a different question from the one clicked.
    const computed = chart();
    computed.query.group_by = [
      { expression: { left: "orders.total", op: "-", right: "orders.item_count" }, as: "country" },
    ];

    expect(() => drill(spec(computed), rows, { category: "Germany" }, STATUS)).toThrow(
      /worked out from other columns/,
    );
  });

  it("refuses a mark the result set no longer holds", () => {
    expect(() => drill(spec(chart()), rows, { category: "Atlantis" }, STATUS)).toThrow(NoDrill);
  });

  it("keeps the rest of the spec, so the measure and the cap survive", () => {
    const narrowed = drill(spec(chart()), rows, { category: "Germany" }, STATUS);

    expect(narrowed.query.aggregates).toEqual([
      { fn: "sum", column: "vizmith.shop.orders.total", as: "revenue" },
    ]);
    expect(narrowed.query.limit).toBe(500);
    expect(narrowed.query.joins).toHaveLength(1);
  });
});
