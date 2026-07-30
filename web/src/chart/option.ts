import type { EChartsOption, SeriesOption } from "echarts";

export type ChannelType = "nominal" | "ordinal" | "quantitative" | "temporal";

export type Channel = {
  field: string;
  type: ChannelType;
  title?: string;
};

export type Spec = {
  title?: string;
  chart: {
    mark: "bar" | "line" | "area" | "point" | "arc";
    stack?: boolean;
    encoding: { x: Channel; y: Channel; color?: Channel };
  };
};

export type Value = string | number | boolean | null;
export type Row = Record<string, Value>;

/** Shown wherever a null reaches an axis or a legend. Left joins produce these. */
export const NO_VALUE = "(no value)";

const AXIS_TYPE = {
  nominal: "category",
  ordinal: "category",
  quantitative: "value",
  temporal: "time",
} as const;

const SERIES_TYPE = { bar: "bar", line: "line", area: "line", point: "scatter" } as const;

const label = (value: Value): string => (value === null ? NO_VALUE : String(value));

const distinct = (values: string[]): string[] => [...new Set(values)];

const axisName = (channel: Channel): string => channel.title ?? channel.field;

/**
 * A validated spec plus its result set becomes an ECharts option. Null means there is
 * nothing to draw, which the caller shows as an empty state.
 *
 * Rows are read by column name only, and never aggregated, sorted, filtered or
 * truncated. All of that already happened in the query.
 */
export function buildOption(spec: Spec, rows: Row[]): EChartsOption | null {
  if (rows.length === 0) return null;

  const { mark, stack, encoding } = spec.chart;
  const { x, y, color } = encoding;
  const title = spec.title ? { text: spec.title } : undefined;

  if (mark === "arc") {
    return {
      title,
      series: [
        {
          type: "pie",
          data: rows.map((row) => ({ name: label(row[x.field]), value: row[y.field] })),
        },
      ] as SeriesOption[],
    };
  }

  const categorical = AXIS_TYPE[x.type] === "category";
  const categories = categorical ? distinct(rows.map((row) => label(row[x.field]))) : [];
  const groups = color ? distinct(rows.map((row) => label(row[color.field]))) : [undefined];

  const series = groups.map((group) => ({
    name: group,
    type: SERIES_TYPE[mark],
    ...(mark === "area" ? { areaStyle: {} } : {}),
    ...(stack ? { stack: "total" } : {}),
    data: seriesData(
      color ? rows.filter((row) => label(row[color.field]) === group) : rows,
      x,
      y,
      categorical ? categories : null,
    ),
  }));

  return {
    title,
    xAxis: {
      type: AXIS_TYPE[x.type],
      name: axisName(x),
      ...(categorical ? { data: categories } : {}),
    },
    yAxis: { type: AXIS_TYPE[y.type], name: axisName(y) },
    series: series as SeriesOption[],
  };
}

/**
 * On a category axis every series carries one entry per category so the axis stays
 * aligned. A category this series has no row for is null, never zero, because a zero
 * in a stack is a visible lie.
 */
function seriesData(
  rows: Row[],
  x: Channel,
  y: Channel,
  categories: string[] | null,
): (Value | Value[])[] {
  if (categories === null) return rows.map((row) => [row[x.field], row[y.field]]);
  return categories.map((category) => {
    const match = rows.find((row) => label(row[x.field]) === category);
    return match ? match[y.field] : null;
  });
}
