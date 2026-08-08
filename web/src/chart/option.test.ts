// @vitest-environment node
//
// This one reads the spec fixtures off disk, and a jsdom `URL` is not a file URL.
// The default environment is jsdom now, so a test that only needs Node says so
// rather than the whole suite paying for the one that does not.
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import Chart from "./Chart";
import {
  buildOption,
  clickedValue,
  instant,
  markText,
  overSeriesLimit,
  seriesCount,
  NO_VALUE,
  SERIES,
  SERIES_LIMIT,
  type Channel,
  type Mark,
  type Row,
  type Spec,
} from "./option";

const FIXTURES = fileURLToPath(new URL("../../../tests/fixtures/specs/valid", import.meta.url));

const fixtures = readdirSync(FIXTURES)
  .filter((name) => name.endsWith(".json"))
  .map((name) => [name, JSON.parse(readFileSync(`${FIXTURES}/${name}`, "utf8")) as Spec] as const);

/**
 * The renderer reads nothing but the encoding fields, and the validator already
 * guarantees those are output columns, so a result set is built from the channels
 * rather than from the query. The unused column proves columns it was not given are
 * ignored.
 */
const sample = (channel: Channel, i: number) => {
  if (channel.type === "temporal") return `2026-0${i + 1}-01`;
  if (channel.type === "quantitative") return (i + 1) * 10;
  return `value ${i + 1}`;
};

const rowsFor = (spec: Spec, count = 4): Row[] => {
  const { x, y, color } = spec.chart.encoding;
  return Array.from({ length: count }, (_, i) => ({
    unused_column: "ignored",
    ...(color ? { [color.field]: sample(color, i % 2) } : {}),
    ...(x ? { [x.field]: sample(x, i) } : {}),
    [y.field]: sample(y, i),
  }));
};

const seriesOf = (option: ReturnType<typeof buildOption>) =>
  (option?.series ?? []) as Record<string, unknown>[];

/** What ECharts would show on hover, taken from the option rather than from the formatter
 * on its own, so a formatter that is never wired in fails here. */
const hover = (option: ReturnType<typeof buildOption>, mark: Mark) =>
  (option?.tooltip as { formatter: (mark: Mark) => string }).formatter(mark);

const axisOf = (option: ReturnType<typeof buildOption>, which: "xAxis" | "yAxis") =>
  option?.[which] as {
    data?: string[];
    axisLabel: Record<string, unknown>;
    splitLine: { lineStyle: { color: string } };
    nameTextStyle: { color: string };
  };

const spec = (chart: Spec["chart"], title = "Test"): Spec => ({ title, chart });

const named = (channel: Channel): string => channel.title ?? channel.field;

const nominal = (field: string): Channel => ({ field, type: "nominal" });
const quantitative = (field: string): Channel => ({ field, type: "quantitative" });

describe("valid fixtures", () => {
  const SERIES_TYPE: Record<string, string> = {
    bar: "bar",
    line: "line",
    area: "line",
    point: "scatter",
    arc: "pie",
  };

  for (const [name, fixture] of fixtures) {
    const { x } = fixture.chart.encoding;

    // A fixture with no x answers a question with no dimension, so there is no plot to
    // build and the two tests below have no axis to read.
    if (x === undefined) {
      it(`${name} builds no option, because it is one figure`, () => {
        expect(buildOption(fixture, rowsFor(fixture, 1))).toBeNull();
      });
      continue;
    }

    it(`${name} renders as its mark`, () => {
      const option = buildOption(fixture, rowsFor(fixture));
      const series = seriesOf(option);

      expect(series.length).toBeGreaterThan(0);
      expect(series[0].type).toBe(SERIES_TYPE[fixture.chart.mark]);
    });

    it(`${name} labels every category and reads back on hover`, () => {
      const rows = rowsFor(fixture, 9);
      const option = buildOption(fixture, rows);
      const { y } = fixture.chart.encoding;
      const categories = option !== null && "xAxis" in option ? axisOf(option, "xAxis").data : undefined;

      if (categories !== undefined) {
        const axis = axisOf(option, "xAxis");
        expect(categories).toEqual(rows.map((row) => String(row[x.field])));
        expect(axis.axisLabel).toMatchObject({ interval: 0, hideOverlap: false, overflow: "truncate" });
        expect(axis.axisLabel.rotate).toBeGreaterThan(0);
      }

      // A category axis names the category, so a mark carries the measure alone. Every
      // other axis carries the pair.
      const first = rows[0];
      const measured = categories !== undefined || !(option !== null && "xAxis" in option);
      const text = hover(option, {
        name: String(first[x.field]),
        value: measured ? first[y.field] : [first[x.field], first[y.field]],
      });

      expect(text).toContain(`${named(x)}: ${first[x.field]}`);
      expect(text).toContain(`${named(y)}: ${first[y.field]}`);
    });
  }

  it("covers every mark", () => {
    const marks = new Set(fixtures.map(([, fixture]) => fixture.chart.mark));
    expect([...marks].sort()).toEqual(["arc", "area", "bar", "line", "point"]);
  });
});

describe("stacking", () => {
  const stacked = (mark: "bar" | "area", stack?: boolean) =>
    seriesOf(
      buildOption(
        spec({
          mark,
          stack,
          encoding: { x: nominal("country"), y: quantitative("revenue"), color: nominal("category") },
        }),
        [
          { country: "A", category: "one", revenue: 1 },
          { country: "A", category: "two", revenue: 2 },
        ],
      ),
    );

  it.each(["bar", "area"] as const)("%s stacks when stack is true", (mark) => {
    expect(stacked(mark, true).every((series) => series.stack === "total")).toBe(true);
  });

  it.each(["bar", "area"] as const)("%s does not stack without it", (mark) => {
    expect(stacked(mark).every((series) => series.stack === undefined)).toBe(true);
  });
});

describe("colour channel", () => {
  const coloured = (rows: Row[]) =>
    seriesOf(
      buildOption(
        spec({
          mark: "bar",
          encoding: { x: nominal("country"), y: quantitative("revenue"), color: nominal("category") },
        }),
        rows,
      ),
    );

  it("makes one series per distinct value", () => {
    const series = coloured([
      { country: "A", category: "one", revenue: 1 },
      { country: "B", category: "one", revenue: 2 },
      { country: "A", category: "two", revenue: 3 },
    ]);

    expect(series.map((s) => s.name)).toEqual(["one", "two"]);
  });

  it("gives a null its own labelled series", () => {
    const series = coloured([
      { country: "A", category: "one", revenue: 1 },
      { country: "B", category: null, revenue: 2 },
    ]);

    expect(series.map((s) => s.name)).toEqual(["one", NO_VALUE]);
  });

  it("leaves a category a series has no row for null, not zero", () => {
    const series = coloured([
      { country: "A", category: "one", revenue: 1 },
      { country: "B", category: "two", revenue: 2 },
    ]);

    expect(series[0].data).toEqual([1, null]);
  });

  it("takes the first row for a category, which is what the scan it replaced took", () => {
    // The scan per category became one pass that indexes the rows. A query grouped by the
    // axis returns one row per category, so a second one is a spec that did not, and what
    // it drew before was the first of them.
    const series = coloured([
      { country: "A", category: "one", revenue: 1 },
      { country: "A", category: "one", revenue: 99 },
      { country: "B", category: "one", revenue: 2 },
    ]);

    expect(series[0].data).toEqual([1, 2]);
  });
});

describe("series colours", () => {
  const categories = (count: number): Row[] =>
    Array.from({ length: count }, (_, i) => ({ country: "A", category: `c${i}`, revenue: i + 1 }));

  const built = (rows: Row[]) =>
    buildOption(
      spec({
        mark: "bar",
        encoding: { x: nominal("country"), y: quantitative("revenue"), color: nominal("category") },
      }),
      rows,
    );

  it("assigns the documented order, in that order", () => {
    expect(built(categories(4))?.color).toEqual([...SERIES.slice(0, 4)]);
  });

  it("takes only as many colours as there are series, so nothing is cycled", () => {
    for (let count = 1; count <= SERIES_LIMIT; count += 1) {
      const assigned = built(categories(count))?.color as string[];
      expect(assigned).toHaveLength(count);
      expect(new Set(assigned).size).toBe(count);
    }
  });

  it("gives a one series chart the first slot", () => {
    const option = buildOption(
      spec({ mark: "bar", encoding: { x: nominal("country"), y: quantitative("revenue") } }),
      [{ country: "A", revenue: 1 }],
    );

    expect(option?.color).toEqual([SERIES[0]]);
  });

  it("colours the slices of an arc, since a slice is what wears one there", () => {
    const option = buildOption(
      spec({ mark: "arc", encoding: { x: nominal("reason"), y: quantitative("returns_count") } }),
      [
        { reason: "damaged", returns_count: 7 },
        { reason: "wrong size", returns_count: 3 },
      ],
    );

    expect(option?.color).toEqual([SERIES[0], SERIES[1]]);
  });

  for (const [name, fixture] of fixtures) {
    if (fixture.chart.encoding.x === undefined) continue;

    it(`${name} takes its colours from the order`, () => {
      const rows = rowsFor(fixture, 4);
      const option = buildOption(fixture, rows);
      const count = seriesCount(fixture, rows);

      expect(option?.color).toEqual([...SERIES.slice(0, count)]);
    });
  }

  it("refuses a chart with more series than the order holds", () => {
    const chart = spec({
      mark: "bar",
      encoding: { x: nominal("country"), y: quantitative("revenue"), color: nominal("category") },
    });
    const rows = categories(SERIES_LIMIT + 1);

    expect(overSeriesLimit(chart, categories(SERIES_LIMIT))).toBeNull();
    expect(overSeriesLimit(chart, rows)).toContain(`${SERIES_LIMIT + 1} series`);
    expect(overSeriesLimit(chart, rows)).toContain("query.limit_by.limit");
  });
});

describe("the legend", () => {
  const withSeries = (names: (string | null)[], title?: string) =>
    buildOption(
      spec(
        {
          mark: "bar",
          encoding: { x: nominal("country"), y: quantitative("revenue"), color: nominal("category") },
        },
        title,
      ),
      names.map((category, i) => ({ country: `A${i}`, category, revenue: i + 1 })),
    );

  it("names every series once there are two of them", () => {
    expect(withSeries(["one", "two"])?.legend).toMatchObject({ data: ["one", "two"] });
  });

  it("draws none for a single series, since a key with one entry is a label", () => {
    expect(withSeries(["one"])?.legend).toBeUndefined();
    expect(
      buildOption(
        spec({ mark: "bar", encoding: { x: nominal("country"), y: quantitative("revenue") } }),
        [{ country: "A", revenue: 1 }],
      )?.legend,
    ).toBeUndefined();
  });

  it("names a null series the way the axis does, not as a blank entry", () => {
    expect(withSeries(["one", null])?.legend).toMatchObject({ data: ["one", NO_VALUE] });
  });

  it("wears ink and never a series colour", () => {
    const legend = withSeries(["one", "two"])?.legend as { textStyle: { color: string } };

    expect(legend.textStyle.color).toBe("#5c6b7a");
    expect(SERIES).not.toContain(legend.textStyle.color);
  });

  it("sits under the header rather than over the plot", () => {
    const titled = withSeries(["one", "two"], "Revenue") as {
      legend: { top: number };
      grid: { top: number };
    };

    expect(titled.grid.top).toBeGreaterThan(titled.legend.top);
  });
});

describe("tooltip", () => {
  const bars = (rows: Row[], color?: Channel) =>
    buildOption(
      spec({ mark: "bar", encoding: { x: nominal("country"), y: quantitative("revenue"), color } }),
      rows,
    );

  it("names the category and the exact value, and nothing else", () => {
    const option = bars([{ country: "Country one", revenue: 20481 }]);

    expect(hover(option, { name: "Country one", value: 20481 })).toBe(
      "country: Country one\nrevenue: 20481",
    );
  });

  it("names the series when a colour channel made one", () => {
    const option = bars([{ country: "A", category: "one", revenue: 1 }], nominal("category"));

    expect(hover(option, { name: "A", seriesName: "one", value: 1 })).toBe(
      "country: A\ncategory: one\nrevenue: 1",
    );
  });

  it("names a null series the way the legend and the axis do", () => {
    const option = bars([{ country: "A", category: null, revenue: 1 }], nominal("category"));

    expect(hover(option, { name: "A", seriesName: NO_VALUE, value: 1 })).toContain(
      `category: ${NO_VALUE}`,
    );
  });

  it("reads a pair off an axis that carries one", () => {
    const option = buildOption(
      spec({
        mark: "line",
        encoding: { x: { field: "month", type: "temporal" }, y: quantitative("order_count") },
      }),
      [{ month: "2026-01-01", order_count: 5 }],
    );

    expect(hover(option, { name: "", value: ["2026-01-01", 5] })).toBe(
      "month: 2026-01-01\norder_count: 5",
    );
  });

  it("reaches an arc too", () => {
    const option = buildOption(
      spec({ mark: "arc", encoding: { x: nominal("reason"), y: quantitative("returns_count") } }),
      [{ reason: "damaged", returns_count: 7 }],
    );

    expect(hover(option, { name: "damaged", value: 7 })).toBe("reason: damaged\nreturns_count: 7");
  });

  it("is drawn as text on the canvas, so a row value is never markup", () => {
    const option = bars([{ country: "<img src=x>", revenue: 1 }]);

    expect(option?.tooltip).toMatchObject({ trigger: "item", renderMode: "richText" });
    expect(hover(option, { name: "<img src=x>", value: 1 })).toBe("country: <img src=x>\nrevenue: 1");
  });

  it("wears the application surface and no series colour", () => {
    const option = bars([{ country: "A", revenue: 1 }]);

    expect(option?.tooltip).toMatchObject({
      backgroundColor: "#ffffff",
      borderColor: "#cfd7df",
      textStyle: { color: "#14202b" },
    });
  });

  it("says the same thing whether it is called through the option or directly", () => {
    const chart = spec({ mark: "bar", encoding: { x: nominal("country"), y: quantitative("revenue") } });
    const mark: Mark = { name: "A", value: 1 };

    expect(hover(buildOption(chart, [{ country: "A", revenue: 1 }]), mark)).toBe(markText(chart, mark));
  });
});

describe("chrome", () => {
  const option = buildOption(
    spec({ mark: "bar", encoding: { x: nominal("country"), y: quantitative("revenue") } }),
    [{ country: "A", revenue: 1 }],
  );

  it.each(["xAxis", "yAxis"] as const)("dresses %s in the tokens", (which) => {
    const axis = axisOf(option, which);

    expect(axis.axisLabel).toMatchObject({ color: "#5c6b7a", fontSize: 11 });
    expect(axis.axisLabel.fontFamily).toContain("ui-monospace");
    expect(axis.nameTextStyle.color).toBe("#8c99a6");
    expect(axis.splitLine.lineStyle.color).toBe("#e1e6ec");
  });

  const countries = (names: string[]) =>
    axisOf(
      buildOption(
        spec({ mark: "bar", encoding: { x: nominal("country"), y: quantitative("revenue") } }),
        names.map((country) => ({ country, revenue: 1 })),
      ),
      "xAxis",
    ).axisLabel;

  it("keeps a category label upright while there is room for it", () => {
    expect(countries(["A", "B", "C"])).toMatchObject({ interval: 0, rotate: 0 });
  });

  it("leans a few long labels over, not only many short ones", () => {
    expect(countries(["Country one", "A distribution centre in Rotterdam"]).rotate).toBeGreaterThan(0);
  });
});

describe("mark geometry", () => {
  const barsWith = (stack?: boolean) =>
    seriesOf(
      buildOption(
        spec({
          mark: "bar",
          stack,
          encoding: { x: nominal("country"), y: quantitative("revenue"), color: nominal("category") },
        }),
        [{ country: "A", category: "one", revenue: 1 }],
      ),
    );

  it("caps a bar and rounds its data end", () => {
    expect(barsWith()[0]).toMatchObject({
      barMaxWidth: 24,
      itemStyle: { borderRadius: [4, 4, 0, 0] },
    });
  });

  it("leaves a stacked segment square", () => {
    expect(barsWith(true)[0].itemStyle).toBeUndefined();
  });

  it("draws a line at two pixels with a ringed marker", () => {
    const series = seriesOf(
      buildOption(
        spec({ mark: "line", encoding: { x: nominal("month"), y: quantitative("orders") } }),
        [{ month: "January", orders: 1 }],
      ),
    );

    expect(series[0]).toMatchObject({
      lineStyle: { width: 2 },
      symbolSize: 8,
      itemStyle: { borderColor: "#ffffff", borderWidth: 2 },
    });
  });
});

it("labels a null on a category axis", () => {
  const option = buildOption(
    spec({ mark: "bar", encoding: { x: nominal("carrier"), y: quantitative("shipment_count") } }),
    [
      { carrier: "Carrier one", shipment_count: 4 },
      { carrier: null, shipment_count: 1 },
    ],
  );

  expect(option?.xAxis).toMatchObject({ data: ["Carrier one", NO_VALUE] });
});

it("maps arc x to the slice label and y to the slice value", () => {
  const option = buildOption(
    spec({ mark: "arc", encoding: { x: nominal("reason"), y: quantitative("returns_count") } }),
    [
      { reason: "damaged", returns_count: 7 },
      { reason: "wrong size", returns_count: 3 },
    ],
  );

  expect(seriesOf(option)[0].data).toEqual([
    { name: "damaged", value: 7 },
    { name: "wrong size", value: 3 },
  ]);
  expect(option).not.toHaveProperty("xAxis");
  expect(option).not.toHaveProperty("yAxis");
});

describe("a temporal axis", () => {
  const MONTHLY = spec({
    mark: "line",
    encoding: { x: { field: "month", type: "temporal" }, y: quantitative("order_count") },
  });
  const ROWS: Row[] = [
    { month: "2026-01-01", order_count: 5 },
    { month: "2026-02-01", order_count: 8 },
  ];

  it("reads both of the shapes the result set contract allows", () => {
    expect(instant("2026-01-01")).toBe(new Date(2026, 0, 1).getTime());
    expect(instant("2026-01-01T09:30:00")).toBe(new Date(2026, 0, 1, 9, 30).getTime());
    expect(instant("2026-01-01T09:30:00.123456")).toBe(new Date(2026, 0, 1, 9, 30, 0, 123).getTime());
  });

  it("reads a date and a midnight timestamp as one moment", () => {
    // The two forms one contract allows, which ECharts' own parser puts a timezone apart:
    // it reads a bare date as UTC and a date with a time as local.
    expect(instant("2026-01-01")).toBe(instant("2026-01-01T00:00:00"));
  });

  it("draws no mark for a value that is not the contract's shape", () => {
    expect(instant("2026-01-01T00:00:00.000Z")).toBeNull();
    expect(instant("January")).toBeNull();
    expect(instant(5)).toBeNull();
  });

  it("plots the moment rather than the text it arrived as", () => {
    const option = buildOption(MONTHLY, ROWS);

    expect(option?.xAxis).toMatchObject({ type: "time" });
    expect(seriesOf(option)[0].data).toEqual([
      [instant("2026-01-01"), 5],
      [instant("2026-02-01"), 8],
    ]);
  });

  it("names the value the source sent on hover, not the moment it plotted", () => {
    const hovered = hover(buildOption(MONTHLY, ROWS), { name: "", value: [instant("2026-01-01"), 5] });

    expect(hovered).toBe("month: 2026-01-01\norder_count: 5");
  });

  it("hands a click the value the source sent, which is what a drill filters on", () => {
    expect(clickedValue(MONTHLY, ROWS, { value: [instant("2026-02-01"), 8] })).toBe("2026-02-01");
  });

  it("hands a click on a category axis the label, the way it always did", () => {
    const bars = spec({ mark: "bar", encoding: { x: nominal("country"), y: quantitative("revenue") } });
    const rows = [{ country: "Netherlands", revenue: 1 }];

    expect(clickedValue(bars, rows, { name: "Netherlands", value: 1 })).toBe("Netherlands");
  });
});

it("titles an axis by its channel title, falling back to the field", () => {
  const option = buildOption(
    spec({
      mark: "bar",
      encoding: {
        x: { field: "country", type: "nominal", title: "Country" },
        y: quantitative("revenue"),
      },
    }),
    [{ country: "A", revenue: 1 }],
  );

  expect(option?.xAxis).toMatchObject({ name: "Country" });
  expect(option?.yAxis).toMatchObject({ name: "revenue" });
  expect(option?.title).toMatchObject({ text: "Test" });
});

it("returns null for an empty result set", () => {
  const [, fixture] = fixtures[0];
  expect(buildOption(fixture, [])).toBeNull();
});

describe("a question with no dimension", () => {
  const figure = (title?: string): Spec => ({
    title,
    chart: { mark: "bar", encoding: { y: { field: "revenue", type: "quantitative", title: "Revenue" } } },
  });

  const drawn = (spec: Spec, rows: Row[]) =>
    renderToStaticMarkup(createElement(Chart, { spec, rows }));

  it("draws the measure as it arrived, unrounded", () => {
    expect(drawn(figure("Total revenue"), [{ revenue: 86132852.71 }])).toContain("86132852.71");
  });

  it("names the figure by the spec title, falling back to the measure", () => {
    expect(drawn(figure("Total revenue"), [{ revenue: 1 }])).toContain("Total revenue");
    expect(drawn(figure(), [{ revenue: 1 }])).toContain("Revenue");
  });

  it("still says there is nothing to draw when the query returned no rows", () => {
    expect(drawn(figure("Total revenue"), [])).toContain("No rows to draw");
  });
});

it("draws an empty state instead of a chart when there are no rows", () => {
  const [, fixture] = fixtures[0];
  const markup = renderToStaticMarkup(createElement(Chart, { spec: fixture, rows: [] }));

  expect(markup).toContain("No rows to draw");
});

it("does not depend on the order of the keys in a row", () => {
  const reverse = (rows: Row[]) =>
    rows.map((row) => Object.fromEntries(Object.entries(row).reverse()) as Row);

  /** The formatter closes over the spec, so two builds give two functions that behave the
   * same and compare unequal. Its output stands in for it. */
  const comparable = (option: ReturnType<typeof buildOption>) => ({
    ...option,
    tooltip: hover(option, { name: "one", value: 1 }),
  });

  for (const [name, fixture] of fixtures) {
    // A figure builds no option, so there is nothing here for a key order to change.
    if (fixture.chart.encoding.x === undefined) continue;
    const rows = rowsFor(fixture);
    expect(comparable(buildOption(fixture, reverse(rows))), name).toEqual(
      comparable(buildOption(fixture, rows)),
    );
  }
});
