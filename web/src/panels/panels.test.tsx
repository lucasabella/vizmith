import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { ColumnProfile, TableProfile } from "../api";
import Table from "../chart/Table";
import Visual from "../chart/Visual";
import type { Row } from "../chart/option";
import type { Spec } from "../spec/spec";
import { SERIES_LIMIT } from "../chart/option";
import Fields, { Profile, Unread, nullRate } from "./Fields";
import { fromProfiles, fromShape } from "./fields";
import Wells from "./Wells";
import { draftIn, place, type Draft, type Field } from "../spec/spec";

const drawn = (element: React.ReactElement) => renderToStaticMarkup(element);

const column = (over: Partial<ColumnProfile> = {}): ColumnProfile => ({
  name: "country",
  type: "string",
  null_rate: 0,
  distinct_count: 14,
  distinct_count_exact: false,
  minimum: null,
  maximum: null,
  samples: ["Belgium", "Germany", "Netherlands"],
  ...over,
});

const profile = (columns: ColumnProfile[]): TableProfile => ({
  table: "vizmith.shop.customers",
  row_count: 2000,
  columns,
});

/** The tree with one table open, which is what the panel renders once a table is
 * expanded. A static render only reaches the closed state, so the open one is built by
 * rendering the column node through a profile with a single column. */
const tree = (columns: ColumnProfile[]) =>
  drawn(<Fields tables={fromProfiles([profile(columns)])} failure={null} onDrag={() => {}} />);

describe("the fields tree", () => {
  it("lists a table with its row count", () => {
    const markup = tree([column()]);

    expect(markup).toContain("customers");
    expect(markup).toContain("2,000 rows");
  });

  it("says so when a source is not connected yet", () => {
    expect(drawn(<Fields tables={[]} failure={null} onDrag={() => {}} />)).toContain(
      "once a source is connected",
    );
  });

  it("shows what the source refused rather than an empty tree", () => {
    expect(drawn(<Fields tables={null} failure="the warehouse said no" onDrag={() => {}} />)).toContain(
      "the warehouse said no",
    );
  });

  it("shows nothing of a column until the tree is opened", () => {
    // The profile is one interaction in, which a static render does not reach. What this
    // holds is that a closed tree is names and counts and no values.
    expect(tree([column()])).not.toContain("Netherlands");
  });
});

describe("the tree before anything has been profiled", () => {
  const shaped = fromShape([
    { table: "vizmith.shop.customers", columns: [{ name: "country", type: "string" }] },
  ]);
  const outline = drawn(<Fields tables={shaped} failure={null} onDrag={() => {}} />);

  it("draws the tree from the shape alone", () => {
    // What is on screen while the profiles are still being read, which on a schema nobody
    // has profiled used to be 25 seconds of spinner. A static render reaches the closed
    // tree, so what it proves is that a table row needs no profile to be drawn; the column
    // rows under it are one interaction in, and `fields.test.ts` holds their contents.
    expect(outline).toContain("customers");
    expect(outline).not.toContain("Reading the schema");
  });

  it("says nothing about a row count it has not read, rather than saying zero", () => {
    // A zero is a claim about the data. This is a claim about what has been read, and they
    // are different sentences.
    expect(outline).not.toContain("0 rows");
    expect(outline).not.toContain("rows");
  });

  it("says a column's figures have not been read rather than drawing them as absent", () => {
    // The panel is what proves what the model was allowed to see. Every figure drawn as
    // absent — no nulls, no distinct values, no vocabulary — is the strongest claim it can
    // make about a column, and it would be making it about one it knows nothing about.
    // The element the tree draws for it, reached directly the way `Profile` is: a static
    // render stops at the closed row.
    const open = drawn(<Unread />);

    expect(open).toContain("have not been read yet");
    expect(open).not.toContain("too many distinct values");
    expect(open).not.toContain("nulls");
  });
});

describe("a column's profile", () => {
  const shown = (over: Partial<ColumnProfile>) => drawn(<Profile column={column(over)} />);

  it("marks a distinct count the source estimated, and leaves an exact one alone", () => {
    expect(shown({ distinct_count_exact: false })).toContain("approx.");
    expect(shown({ distinct_count_exact: true })).not.toContain("approx.");
  });

  it("says why a column has no samples rather than showing an empty list", () => {
    const markup = shown({ samples: [], distinct_count: 2308 });

    expect(markup).toContain("too many distinct values");
    expect(markup).toContain("2,308");
  });

  it("lists the vocabulary of a low cardinality column", () => {
    expect(shown({})).toContain("Netherlands");
  });

  it("shows a range where the type has one", () => {
    expect(shown({ type: "date", minimum: "2024-01-01", maximum: "2026-06-30" })).toContain("2024-01-01");
    expect(shown({})).not.toContain("range");
  });

  it("never rounds a null rate that is not zero down to none", () => {
    expect(nullRate(0)).toBe("none");
    expect(nullRate(0.000001)).toBe("<0.1%");
    expect(nullRate(0.034)).toBe("3.4%");
    expect(nullRate(0.5)).toBe("50%");
    expect(nullRate(1)).toBe("all");
  });
});

describe("the table view", () => {
  const rows: Row[] = [
    { country: "Netherlands", revenue: 91240.5, orders: 12 },
    { country: null, revenue: 3, orders: 1 },
  ];

  it("shows the output columns in the order the builder emitted them", () => {
    const markup = drawn(<Table rows={rows} />);
    const order = ["country", "revenue", "orders"].map((name) => markup.indexOf(`>${name}<`));

    expect(order).toEqual([...order].sort((a, b) => a - b));
    expect(order[0]).toBeGreaterThan(-1);
  });

  it("prints a value as it arrived, and a null the way the axis does", () => {
    const markup = drawn(<Table rows={rows} />);

    expect(markup).toContain("91240.5");
    expect(markup).toContain("(no value)");
  });

  it("gives a column of figures tabular numerals and the right edge", () => {
    expect(drawn(<Table rows={rows} />)).toContain("table__td--figure");
  });

  it("judges a column once for the result set, and judges it the same way", () => {
    // Decided per cell before this, which was a walk of every row for every cell drawn.
    // What has to survive that: a column of numbers and nulls is figures, a column with
    // anything else in it is not, and a column of nothing but nulls is not either.
    const mixed: Row[] = [
      { country: "Netherlands", revenue: 1, note: null, code: "A1" },
      { country: null, revenue: null, note: null, code: 2 },
    ];
    const markup = drawn(<Table rows={mixed} />);
    const figure = (column: string) =>
      markup.includes(`<th class="table__th--figure">${column}</th>`);

    expect(figure("revenue")).toBe(true);
    expect(figure("country")).toBe(false);
    expect(figure("note")).toBe(false);
    expect(figure("code")).toBe(false);
  });
});

const COUNTRY: Field = { table: "vizmith.shop.customers", column: "country", type: "string" };
const CATEGORY: Field = { table: "vizmith.shop.products", column: "category", type: "string" };
const TOTAL: Field = { table: "vizmith.shop.orders", column: "total", type: "decimal" };
const ORDERED: Field = { table: "vizmith.shop.orders", column: "order_date", type: "date" };

const wells = (draft: Draft | null) =>
  drawn(<Wells draft={draft} dragging={null} onChange={() => {}} onRelationships={() => {}} />);

describe("the wells", () => {
  const revenue = place(place(null, "Axis", COUNTRY), "Values", TOTAL);

  it("offers a drop zone for every well while they are empty", () => {
    expect(wells(null).match(/Drop a field here/g)).toHaveLength(5);
  });

  it("goes quiet for JSON that parses and is not a spec, rather than taking the tab down", () => {
    // The consumer half of `draftIn`. `{"a":1}` reaches here as null, and null is the
    // state these panels have always drawn: reading `draft.chart.encoding` off it threw
    // during render, which in React 19 unmounts the whole application.
    expect(() => wells(draftIn('{"a":1}'))).not.toThrow();
    expect(wells(draftIn('{"a":1}'))).toBe(wells(null));
  });

  it("shows what is in a well by the name the result set uses", () => {
    const markup = wells(revenue);

    expect(markup).toContain("country");
    expect(markup).toContain("total");
  });

  it("shows the aggregate it inferred, as something that can be changed", () => {
    const markup = wells(revenue);

    expect(markup).toContain('<option value="sum" selected');
    expect(markup).toContain('value="count_distinct"');
  });

  it("shows the date unit it inferred", () => {
    const markup = wells(place(null, "Axis", ORDERED));

    expect(markup).toContain('<option value="month" selected');
    expect(markup).toContain("every value");
  });

  it("says nothing about Top N until the chart has a legend", () => {
    expect(wells(revenue)).not.toContain("Required");
  });

  it("marks Top N as missing, in red, once there is a legend and no ranking", () => {
    const markup = wells(place(revenue, "Legend", CATEGORY));

    expect(markup).toContain("Missing");
    expect(markup).toContain("well__drop--missing");
  });

  it("reads Required once the ranking is there", () => {
    const ranked = place(place(revenue, "Legend", CATEGORY), "Top N", COUNTRY);
    const markup = wells(ranked);

    expect(markup).toContain("Required");
    expect(markup).not.toContain("Missing");
    expect(markup).toContain("country by total");
  });

  it("offers a way back out of every well it filled", () => {
    expect(wells(revenue)).toContain("Remove from Axis");
    expect(wells(revenue)).toContain("Remove from Values");
  });
});

describe("the visual card", () => {
  const spec: Spec = {
    spec_version: "1",
    title: "Revenue per country",
    query: { from: "orders", limit: 500 },
    chart: {
      mark: "bar",
      encoding: {
        x: { field: "country", type: "nominal" },
        y: { field: "revenue", type: "quantitative" },
        color: { field: "category", type: "nominal" },
      },
    },
  };

  const many = (count: number): Row[] =>
    Array.from({ length: count }, (_, i) => ({ country: "A", category: `c${i}`, revenue: i }));

  it("carries the Chart and Table control", () => {
    const markup = drawn(<Visual spec={spec} rows={many(2)} columns={[]} onDrill={() => {}} />);

    expect(markup).toContain(">Chart<");
    expect(markup).toContain(">Table<");
  });

  it("refuses to draw more series than there are colours, and says what to lower", () => {
    const markup = drawn(
      <Visual spec={spec} rows={many(SERIES_LIMIT + 1)} columns={[]} onDrill={() => {}} />,
    );

    expect(markup).toContain(`${SERIES_LIMIT + 1} series`);
    expect(markup).toContain("query.limit_by.limit");
  });
});
