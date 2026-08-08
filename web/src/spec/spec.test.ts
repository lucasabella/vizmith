import { describe, expect, it } from "vitest";
import {
  WellRefusal,
  aliasFor,
  clear,
  draftIn,
  inQuery,
  outputColumns,
  place,
  qualified,
  ranksRequired,
  reaggregate,
  retop,
  retruncate,
  type Draft,
  type Field,
} from "./spec";

const field = (table: string, column: string, type: string): Field => ({ table, column, type });

const COUNTRY = field("vizmith.shop.customers", "country", "string");
const CATEGORY = field("vizmith.shop.products", "category", "string");
const TOTAL = field("vizmith.shop.orders", "total", "decimal");
const ORDERED = field("vizmith.shop.orders", "order_date", "date");
const STATUS = field("vizmith.shop.orders", "status", "string");

/** A chart somebody built by dragging: a measure and a dimension, nothing else. */
const revenue = (): Draft => place(place(null, "Axis", COUNTRY), "Values", TOTAL);

describe("a column into a well", () => {
  it("starts a spec from the table it came from", () => {
    const draft = place(null, "Axis", COUNTRY);

    expect(draft.query.from).toBe("vizmith.shop.customers");
    expect(draft.spec_version).toBe("1");
    expect(draft.query.limit).toBeGreaterThan(0);
  });

  it("groups by the column and binds it to the axis", () => {
    const draft = place(null, "Axis", COUNTRY);

    expect(draft.query.group_by).toEqual([
      { column: "vizmith.shop.customers.country", as: "country" },
    ]);
    expect(draft.chart.encoding.x).toEqual({ field: "country", type: "nominal" });
  });

  it("aggregates a measure and binds it to the value axis", () => {
    const draft = revenue();

    expect(draft.query.aggregates).toEqual([
      { fn: "sum", column: "vizmith.shop.orders.total", as: "total" },
    ]);
    expect(draft.chart.encoding.y).toEqual({ field: "total", type: "quantitative" });
  });

  it("counts a column it cannot sum", () => {
    const draft = place(null, "Values", STATUS);

    expect(draft.query.aggregates?.[0].fn).toBe("count");
  });

  it("truncates a date on an axis, which is the other thing it infers", () => {
    const draft = place(null, "Axis", ORDERED);

    expect(draft.query.group_by?.[0]).toMatchObject({ truncate: "month" });
    expect(draft.chart.encoding.x?.type).toBe("temporal");
  });

  it("carries the joins it was given for a column on another table", () => {
    const joins = [
      { table: "vizmith.shop.orders", on: [{ left: "a.customer_id", right: "b.id" }] },
    ];
    const draft = place(revenue(), "Legend", CATEGORY, joins);

    expect(draft.query.joins).toEqual(joins);
  });

  it("does not add a join for a table the query already reads", () => {
    const orders = place(null, "Axis", STATUS);
    const draft = place(orders, "Values", TOTAL, [
      { table: "vizmith.shop.orders", on: [{ left: "a.id", right: "b.id" }] },
    ]);

    expect(draft.query.from).toBe("vizmith.shop.orders");
    expect(draft.query.joins).toBeUndefined();
  });

  it("replaces what a well already held rather than stacking on it", () => {
    const draft = place(revenue(), "Axis", STATUS);

    expect(draft.query.group_by).toEqual([{ column: "vizmith.shop.orders.status", as: "status" }]);
    expect(draft.chart.encoding.x?.field).toBe("status");
  });

  it("filters out the nulls, which is the one filter that needs no value", () => {
    const draft = place(revenue(), "Filters", STATUS);

    expect(draft.query.filters).toEqual([
      { column: "vizmith.shop.orders.status", op: "is_not_null" },
    ]);
  });

  it("ranks by the measure the chart already has", () => {
    const draft = place(place(revenue(), "Legend", CATEGORY), "Top N", COUNTRY);

    expect(draft.query.limit_by).toEqual({
      column: "country",
      by: "total",
      limit: 10,
      direction: "desc",
    });
  });

  it("refuses a Top N on a column the chart does not group by", () => {
    expect(() => place(revenue(), "Top N", CATEGORY)).toThrow(WellRefusal);
  });
});

describe("what a well produces is a spec somebody could have typed", () => {
  it("never produces the same output column twice", () => {
    const draft = place(place(null, "Axis", field("shop.a", "id", "integer")), "Legend", {
      table: "shop.b",
      column: "id",
      type: "integer",
    });
    const names = outputColumns(draft.query);

    expect(new Set(names).size).toBe(names.length);
    expect(names).toEqual(["id", "b_id"]);
  });

  it("qualifies a clashing name with its table", () => {
    expect(aliasFor(COUNTRY, [])).toBe("country");
    expect(aliasFor(COUNTRY, ["country"])).toBe("customers_country");
    expect(aliasFor(COUNTRY, ["country", "customers_country"])).toBe("customers_country_2");
  });
});

describe("taking a column back out", () => {
  it("empties the well and the query with it", () => {
    const draft = clear(revenue(), "Axis");

    expect(draft.chart.encoding.x).toBeUndefined();
    expect(draft.query.group_by).toBeUndefined();
  });

  it("takes the ranking with it, since it named the column that left", () => {
    const ranked = place(place(place(revenue(), "Legend", CATEGORY), "Top N", COUNTRY), "Axis", STATUS);

    expect(ranked.query.limit_by).toBeUndefined();
  });

  it("leaves a filter it did not remove alone", () => {
    const two = place(place(revenue(), "Filters", STATUS), "Filters", COUNTRY);
    const one = clear(two, "Filters", 0);

    expect(one.query.filters).toEqual([{ column: "vizmith.shop.customers.country", op: "is_not_null" }]);
  });
});

describe("what a well inferred is what a well changes", () => {
  it("re-aggregates the measure", () => {
    expect(reaggregate(revenue(), "avg").query.aggregates?.[0].fn).toBe("avg");
  });

  it("re-truncates a date, and takes the unit off entirely", () => {
    const monthly = place(null, "Axis", ORDERED);

    expect(retruncate(monthly, "x", "year").query.group_by?.[0].truncate).toBe("year");
    expect(retruncate(monthly, "x", null).query.group_by?.[0].truncate).toBeUndefined();
  });

  it("changes how many the ranking keeps", () => {
    const ranked = place(place(revenue(), "Legend", CATEGORY), "Top N", COUNTRY);

    expect(retop(ranked, 5).query.limit_by?.limit).toBe(5);
  });
});

describe("the rule the Top N well shows", () => {
  it("is required as soon as the chart has a legend", () => {
    expect(ranksRequired(revenue())).toBe(false);
    expect(ranksRequired(place(revenue(), "Legend", CATEGORY))).toBe(true);
  });
});

describe("whether a column needs a join at all", () => {
  it("matches a table the query reads on any trailing part of its name", () => {
    const draft = revenue();

    expect(inQuery(draft.query, COUNTRY)).toBe(true);
    expect(inQuery(draft.query, CATEGORY)).toBe(false);
    // A spec names a table on one level where a profile names it on three, and both are
    // the same table. The validator resolves it that way, so this has to as well.
    expect(inQuery({ ...draft.query, from: "customers" }, COUNTRY)).toBe(true);
    expect(inQuery({ from: "vizmith.shop.customers", limit: 1 }, { ...COUNTRY, table: "shop.customers" })).toBe(
      true,
    );
    expect(inQuery({ from: "other.shop.customers", limit: 1 }, COUNTRY)).toBe(false);
  });

  it("writes a column with the name the query already uses for its table", () => {
    const short = { from: "customers", limit: 500 };

    expect(qualified(COUNTRY, short)).toBe("customers.country");
    expect(qualified(COUNTRY)).toBe("vizmith.shop.customers.country");
    expect(qualified(CATEGORY, short)).toBe("vizmith.shop.products.category");
  });
});

describe("what the editor is holding", () => {
  const revenueText = () => JSON.stringify(revenue());

  it("reads a spec out of the text both views write", () => {
    expect(draftIn(revenueText())).toEqual(revenue());
  });

  it("holds nothing for text that is not JSON, so the wells go quiet", () => {
    expect(draftIn("")).toBeNull();
    expect(draftIn("{")).toBeNull();
    expect(draftIn('{"query": {},')).toBeNull();
  });

  it("holds nothing for JSON that parses and is not a spec", () => {
    // The bug. Every one of these used to arrive in the wells as a draft with a `chart`
    // the checker had been told about and the value did not have, and reading it threw
    // during render — which unmounts the tree, so the whole interface went blank.
    expect(draftIn('{"a":1}')).toBeNull();
    expect(draftIn("null")).toBeNull();
    expect(draftIn("42")).toBeNull();
    expect(draftIn('"a spec"')).toBeNull();
    expect(draftIn("[]")).toBeNull();
  });

  it("holds nothing while the parts a panel reads are still missing", () => {
    expect(draftIn('{"query":{"from":"orders","limit":1}}')).toBeNull();
    expect(draftIn('{"query":{},"chart":{"mark":"bar"}}')).toBeNull();
    expect(draftIn('{"chart":{"encoding":{}}}')).toBeNull();
  });

  it("judges the shape and not the spec, which is the validator's job", () => {
    // A spec with no measure, an unknown mark and a query that names nothing is a spec the
    // validator will refuse and the wells can still draw. A second opinion in the browser
    // is one that can disagree with the one that counts.
    expect(draftIn('{"query":{},"chart":{"mark":"sculpture","encoding":{}}}')).not.toBeNull();
  });
});
