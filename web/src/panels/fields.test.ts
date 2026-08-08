import { describe, expect, it } from "vitest";
import type { TableProfile, TableShape } from "../api";
import { fromProfiles, fromShape, merged } from "./fields";

const shape: TableShape[] = [
  { table: "vizmith.shop.customers", columns: [{ name: "country", type: "string" }] },
  { table: "vizmith.shop.orders", columns: [{ name: "total", type: "decimal" }] },
];

const profiled: TableProfile[] = [
  {
    table: "vizmith.shop.customers",
    row_count: 2000,
    columns: [
      {
        name: "country",
        type: "string",
        null_rate: 0,
        distinct_count: 14,
        distinct_count_exact: false,
        minimum: null,
        maximum: null,
        samples: ["Belgium"],
      },
    ],
  },
];

describe("the tree the panel is drawn from", () => {
  it("carries a name and a type from the shape, which is what a drag reads", () => {
    const [customers] = fromShape(shape);

    expect(customers.table).toBe("vizmith.shop.customers");
    expect(customers.columns).toEqual([{ name: "country", type: "string", profile: null }]);
  });

  it("has no row count until a profile has been read", () => {
    // Nulls rather than zeroes, both of them. A zero row count and an empty profile are
    // claims about the data; these are claims about what has been read.
    expect(fromShape(shape).map((table) => table.row_count)).toEqual([null, null]);
    expect(fromShape(shape).every((table) => table.columns.every((c) => c.profile === null))).toBe(true);
  });

  it("carries the profile once it has", () => {
    const [customers] = fromProfiles(profiled);

    expect(customers.row_count).toBe(2000);
    expect(customers.columns[0].profile?.samples).toEqual(["Belgium"]);
  });

  it("prefers the profile wherever there is one", () => {
    const filled = merged(fromShape(shape), fromProfiles(profiled));

    expect(filled.map((table) => table.row_count)).toEqual([2000, null]);
  });

  it("keeps a table the profiles could not read, rather than dropping it from the tree", () => {
    // A view is the case: `DESCRIBE DETAIL` will not describe one, so the shape has it and
    // the profiles do not. Dropping it would be the panel quietly disagreeing with the
    // schema about which tables exist.
    const filled = merged(fromShape(shape), fromProfiles(profiled));

    expect(filled.map((table) => table.table)).toEqual([
      "vizmith.shop.customers",
      "vizmith.shop.orders",
    ]);
  });

  it("keeps the source's listing order, so the tree does not rearrange as it fills", () => {
    const backwards = fromProfiles([...profiled].reverse());

    expect(merged(fromShape(shape), backwards).map((table) => table.table)).toEqual(
      shape.map((table) => table.table),
    );
  });

  it("shows a table the profiles found and the shape did not", () => {
    // A table created between the two requests. The listing is not held, which is how one
    // is noticed, so the profiles can legitimately know about one this shape does not.
    const filled = merged([], fromProfiles(profiled));

    expect(filled.map((table) => table.table)).toEqual(["vizmith.shop.customers"]);
  });
});
