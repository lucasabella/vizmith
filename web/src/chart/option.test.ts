import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import Chart from "./Chart";
import { buildOption, NO_VALUE, type Channel, type Row, type Spec } from "./option";

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
    [x.field]: sample(x, i),
    [y.field]: sample(y, i),
  }));
};

const seriesOf = (option: ReturnType<typeof buildOption>) =>
  (option?.series ?? []) as Record<string, unknown>[];

const spec = (chart: Spec["chart"], title = "Test"): Spec => ({ title, chart });

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
    it(`${name} renders as its mark`, () => {
      const option = buildOption(fixture, rowsFor(fixture));
      const series = seriesOf(option);

      expect(series.length).toBeGreaterThan(0);
      expect(series[0].type).toBe(SERIES_TYPE[fixture.chart.mark]);
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

it("gives a temporal x a time axis", () => {
  const option = buildOption(
    spec({
      mark: "line",
      encoding: { x: { field: "month", type: "temporal" }, y: quantitative("order_count") },
    }),
    [
      { month: "2026-01-01", order_count: 5 },
      { month: "2026-02-01", order_count: 8 },
    ],
  );

  expect(option?.xAxis).toMatchObject({ type: "time" });
  expect(seriesOf(option)[0].data).toEqual([
    ["2026-01-01", 5],
    ["2026-02-01", 8],
  ]);
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
  expect(option?.title).toEqual({ text: "Test" });
});

it("returns null for an empty result set", () => {
  const [, fixture] = fixtures[0];
  expect(buildOption(fixture, [])).toBeNull();
});

it("draws an empty state instead of a chart when there are no rows", () => {
  const [, fixture] = fixtures[0];
  const markup = renderToStaticMarkup(createElement(Chart, { spec: fixture, rows: [] }));

  expect(markup).toContain("No rows to draw");
});

it("does not depend on the order of the keys in a row", () => {
  const reverse = (rows: Row[]) =>
    rows.map((row) => Object.fromEntries(Object.entries(row).reverse()) as Row);

  for (const [name, fixture] of fixtures) {
    const rows = rowsFor(fixture);
    expect(buildOption(fixture, reverse(rows)), name).toEqual(buildOption(fixture, rows));
  }
});
