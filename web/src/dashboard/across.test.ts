import { describe as group, expect, it } from "vitest";
import type { Spec } from "../spec/spec";
import { conditionsOf, describe, dimensions, narrowed, reach, typeOf } from "./across";

/**
 * The rewrite one dashboard filter makes to each tile it reaches, and the answer it gives
 * about the ones it does not.
 *
 * The interesting half is not the appending. It is the two things this must never do: reach
 * a tile through a join nobody confirmed, and change a tile's stored spec. Both are asserted
 * here rather than inferred from the code, because both would produce a plausible number
 * rather than an error.
 */

const chart = (from: string, group_by: string[], joins: string[] = []): Spec =>
  ({
    spec_version: "1",
    title: `by ${group_by.join(" and ")}`,
    query: {
      from,
      ...(joins.length === 0
        ? {}
        : { joins: joins.map((table) => ({ table, on: [{ left: `${table}.id`, right: `${from}.id` }] })) }),
      group_by: group_by.map((column) => ({ column, as: column.split(".").slice(-1)[0] })),
      aggregates: [{ fn: "sum", column: `${from}.total`, as: "revenue" }],
      limit: 500,
    },
    chart: { mark: "bar", encoding: { y: { field: "revenue", type: "quantitative" } } },
  }) as unknown as Spec;

const ORDERS = chart("vizmith.shop.orders", ["vizmith.shop.orders.status"]);
const CARRIERS = chart("vizmith.shop.carriers", ["vizmith.shop.carriers.name"]);
const SHIPPED = { column: "vizmith.shop.orders.status", op: "=" as const, value: "shipped" };

group("a filter applied to a tile", () => {
  it("goes into the query it reaches, beside whatever was already there", () => {
    const { spec, missed } = narrowed(ORDERS, [SHIPPED]);

    expect(spec.query.filters).toEqual([SHIPPED]);
    expect(missed).toEqual([]);
  });

  it("is added to the filters a tile already had rather than replacing them", () => {
    const already = { column: "vizmith.shop.orders.total", op: ">" as const, value: 0 };
    const withOne = { ...ORDERS, query: { ...ORDERS.query, filters: [already] } };

    expect(narrowed(withOne, [SHIPPED]).spec.query.filters).toEqual([already, SHIPPED]);
  });

  /** The one thing this must not do. A tile that does not read the table would need a join
   * to be filtered by it, and a join a control invented is the failure the whole design
   * exists to prevent: it produces a plausible number rather than an error. */
  it("leaves a tile that does not read the table exactly as it was, and says which filter", () => {
    const { spec, missed } = narrowed(CARRIERS, [SHIPPED]);

    expect(spec).toBe(CARRIERS);
    expect(missed).toEqual([SHIPPED]);
  });

  it("reaches a tile that reads the table through a join, since the query already has one", () => {
    const joined = chart("vizmith.shop.customers", ["vizmith.shop.customers.country"], [
      "vizmith.shop.orders",
    ]);

    expect(narrowed(joined, [SHIPPED]).missed).toEqual([]);
  });

  /** The stored spec is the question somebody built. A dashboard filter is a narrowing of
   * the page that has to come off again without leaving a trace in it. */
  it("does not touch the spec it was given", () => {
    const before = JSON.stringify(ORDERS);

    narrowed(ORDERS, [SHIPPED]);

    expect(JSON.stringify(ORDERS)).toBe(before);
  });

  /** A tile fetches on the spec it is handed, so a fresh object where nothing applied would
   * be a query on every render. */
  it("hands back the same object where nothing applied", () => {
    expect(narrowed(ORDERS, []).spec).toBe(ORDERS);
  });

  it("writes the column the way the query names its own tables", () => {
    const short = chart("shop.orders", ["shop.orders.status"]);

    const [filter] = narrowed(short, [SHIPPED]).spec.query.filters ?? [];

    expect(filter).toEqual({ ...SHIPPED, column: "shop.orders.status" });
  });

  it("reaches nothing with a column that names no table, which the store also refuses", () => {
    expect(narrowed(ORDERS, [{ column: "status", op: "=", value: "shipped" }]).missed).toHaveLength(1);
  });

  /** An `or` with one side dropped is a wider question than the one that was written, and a
   * filter that silently widened is worse than one that says it did not apply. */
  it("applies a disjunction only where every one of its conditions reaches", () => {
    const half = {
      any: [SHIPPED, { column: "vizmith.shop.carriers.name", op: "=" as const, value: "DHL" }],
    };

    expect(narrowed(ORDERS, [half]).missed).toEqual([half]);
    expect(narrowed(ORDERS, [{ any: [SHIPPED, { ...SHIPPED, value: "packed" }] }]).missed).toEqual([]);
  });
});

group("what the bar is drawn from", () => {
  it("offers the dimensions the tiles are grouped by, once each and in one order", () => {
    expect(dimensions([{ spec: ORDERS }, { spec: ORDERS }, { spec: CARRIERS }])).toEqual([
      "vizmith.shop.carriers.name",
      "vizmith.shop.orders.status",
    ]);
  });

  it("qualifies a dimension a query wrote bare, since the list is matched against every tile", () => {
    const bare = chart("vizmith.shop.orders", ["status"]);

    expect(dimensions([{ spec: bare }])).toEqual(["vizmith.shop.orders.status"]);
  });

  it("offers no measure, because a condition on one is having and runs after the grouping", () => {
    expect(dimensions([{ spec: ORDERS }])).not.toContain("vizmith.shop.orders.total");
  });

  it("offers nothing from a computed dimension, which has no column to filter", () => {
    const computed = {
      ...ORDERS,
      query: {
        ...ORDERS.query,
        group_by: [{ expression: { left: "orders.a", op: "-" as const, right: "orders.b" }, as: "gap" }],
      },
    } as unknown as Spec;

    expect(dimensions([{ spec: computed }])).toEqual([]);
  });

  it("counts how many tiles a filter reaches, so one that reaches none says so", () => {
    expect(reach([{ spec: ORDERS }, { spec: CARRIERS }], SHIPPED)).toBe(1);
    expect(reach([{ spec: CARRIERS }], SHIPPED)).toBe(0);
  });

  it("reads a column's type off the profiles, matching however many segments a spec used", () => {
    const columns = [{ table: "vizmith.shop.orders", column: "order_date", type: "date" }];

    expect(typeOf("orders.order_date", columns)).toBe("date");
    expect(typeOf("vizmith.shop.orders.order_date", columns)).toBe("date");
    expect(typeOf("vizmith.shop.orders.status", columns)).toBe("");
  });
});

group("what a chip says", () => {
  it("names the column by its last segment and the operator as a person reads it", () => {
    expect(describe(SHIPPED)).toBe("status = shipped");
    expect(describe({ column: "a.b.shipped_at", op: "is_null" })).toBe("shipped_at is null");
    expect(describe({ column: "a.b.total", op: ">=", value: 500 })).toBe("total ≥ 500");
  });

  it("says a relative value in words, because the tokens are a closed set", () => {
    const date = "vizmith.shop.orders.order_date";

    expect(describe({ column: date, op: ">=", value: { relative: "today" } })).toBe("order_date ≥ today");
    expect(describe({ column: date, op: ">=", value: { relative: "ago", unit: "month", count: 3 } })).toBe(
      "order_date ≥ 3 months ago",
    );
    expect(describe({ column: date, op: ">=", value: { relative: "ago", unit: "day", count: 1 } })).toBe(
      "order_date ≥ 1 day ago",
    );
    expect(describe({ column: date, op: ">=", value: { relative: "start_of", unit: "quarter" } })).toBe(
      "order_date ≥ the start of this quarter",
    );
  });

  it("reads a disjunction as the or it is", () => {
    expect(describe({ any: [SHIPPED, { ...SHIPPED, value: "packed" }] })).toBe(
      "status = shipped or status = packed",
    );
  });

  it("flattens a filter to its conditions the way the validator does", () => {
    expect(conditionsOf(SHIPPED)).toEqual([SHIPPED]);
    expect(conditionsOf({ any: [SHIPPED] })).toEqual([SHIPPED]);
  });
});
