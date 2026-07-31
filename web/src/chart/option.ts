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
    // No x is a question with no dimension. The validator has already established that the
    // query returns one row, and `Chart` draws the measure as a figure rather than a plot.
    encoding: { x?: Channel; y: Channel; color?: Channel };
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

/**
 * ECharts paints onto a canvas and cannot read a CSS variable, so the chrome the chart
 * wears is written out here. These mirror `styles/tokens.css` and go stale if that file
 * moves without this one. Reading the computed styles instead would give one copy, at
 * the price of a DOM in every test of this file.
 */
const SURF = "#ffffff";
const INK = "#14202b";
const INK_2 = "#5c6b7a";
const INK_3 = "#8c99a6";
const RULE = "#e1e6ec";
const RULE_2 = "#cfd7df";
const UI = '"Avenir Next", "Segoe UI", Roboto, system-ui, sans-serif';
const MONO = 'ui-monospace, "SF Mono", SFMono-Regular, Menlo, Consolas, monospace';

/** A category name and a figure both come out of the warehouse, so both are mono. */
const LABEL = { fontFamily: MONO, fontSize: 11, color: INK_2 };

/**
 * The series colours, in the order they are assigned, which is the order and not a
 * palette to pick from. The first three are the ones the application tokens carry and are
 * validated against these surfaces; all eight are the data viz skill's categorical order,
 * whose sequence is the colourblind safety mechanism rather than a preference. Assigning
 * out of order breaks the adjacent pair gates that order was chosen to clear.
 *
 * Never cycled. ECharts cycles its own palette by default, which is what puts one colour
 * on two entries of one legend: a chart that lies about which series it is.
 */
export const SERIES = [
  "#2a78d6",
  "#eb6834",
  "#1baf7a",
  "#eda100",
  "#e87ba4",
  "#008300",
  "#4a3aa7",
  "#e34948",
] as const;

/**
 * Past the end of the order there is no ninth colour, and this refuses rather than
 * inventing one. Folding the tail into "Other" would be aggregating in the renderer,
 * which is the one thing the renderer does not do, and cycling would repaint a series in
 * a colour another series already wears. So a chart with more series than the order holds
 * is not drawn, and the message names the cap and the knob that sets it. See ROADMAP.md.
 */
export const SERIES_LIMIT = SERIES.length;

/**
 * When a category label leans over. `buildOption` never sees the width it will be drawn
 * in, so this keys on the two things it does know: how many labels there are, and how
 * long the longest one is. Six labels of thirty characters collide as badly as fifteen
 * short ones.
 */
const ROTATE_ABOVE = 8;
const ROTATE_LONGER_THAN = 12;

/** How a row value is written wherever one is shown, so an axis, a tooltip and a figure
 * cannot disagree about what came out of the warehouse. Never rounded. */
export const label = (value: Value): string => (value === null ? NO_VALUE : String(value));

const distinct = (values: string[]): string[] => [...new Set(values)];

const axisName = (channel: Channel): string => channel.title ?? channel.field;

/**
 * A validated spec plus its result set becomes an ECharts option. Null means there is
 * nothing to draw, which the caller shows as an empty state.
 *
 * Rows are read by column name only, and never aggregated, sorted, filtered or
 * truncated. All of that already happened in the query.
 */
/**
 * How many series a spec plus its result set produces, which is one per distinct value of
 * the colour channel, or one where there is no colour channel. An arc is one series of
 * many slices and is counted as the slices, because a slice is what wears a colour there.
 */
export function seriesCount(spec: Spec, rows: Row[]): number {
  const { color } = spec.chart.encoding;
  const { x } = spec.chart.encoding;
  if (spec.chart.mark === "arc" && x !== undefined) {
    return distinct(rows.map((row) => label(row[x.field]))).length;
  }
  return color ? distinct(rows.map((row) => label(row[color.field]))).length : 1;
}

/** What is drawn instead of a chart with more series than there are colours. Returned
 * rather than thrown, because the caller shows it the way it shows every other refusal. */
export function overSeriesLimit(spec: Spec, rows: Row[]): string | null {
  const count = seriesCount(spec, rows);
  if (count <= SERIES_LIMIT) return null;
  const knob = spec.chart.encoding.color ? "query.limit_by.limit" : "query.limit";
  return (
    `This chart has ${count} series and there are ${SERIES_LIMIT} colours to tell them apart ` +
    `with. Lower ${knob} to ${SERIES_LIMIT} or fewer and run it again.`
  );
}

export function buildOption(spec: Spec, rows: Row[]): EChartsOption | null {
  if (rows.length === 0) return null;

  const { mark, stack, encoding } = spec.chart;
  const { x, y, color } = encoding;
  // A question with no dimension has one measure and no axes, so there is no option to
  // build. `Chart` draws that as a figure.
  if (x === undefined) return null;
  const title = spec.title
    ? {
        text: spec.title,
        left: 0,
        textStyle: { fontFamily: UI, fontSize: 15.5, fontWeight: 600, color: INK },
      }
    : undefined;
  const tooltip = {
    trigger: "item",
    // Row values land in here, so the tooltip is drawn as text on the canvas. An HTML
    // tooltip would put a warehouse value into the document as markup.
    renderMode: "richText",
    backgroundColor: SURF,
    borderColor: RULE_2,
    borderWidth: 1,
    padding: [6, 8] as [number, number],
    textStyle: { fontFamily: MONO, fontSize: 11.5, color: INK },
    formatter: (params: unknown) => markText(spec, params as Mark),
  } as const;

  if (mark === "arc") {
    const slices = rows.map((row) => label(row[x.field]));
    return {
      title,
      tooltip,
      // A slice is what wears a colour here, so the order is assigned across the slices
      // rather than across the one series holding them.
      color: [...SERIES.slice(0, slices.length)],
      ...legend(slices, Boolean(title)),
      series: [
        {
          type: "pie",
          label: LABEL,
          labelLine: { lineStyle: { color: RULE_2 } },
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
    ...markStyle(mark, stack),
    data: seriesData(
      color ? rows.filter((row) => label(row[color.field]) === group) : rows,
      x,
      y,
      categorical ? categories : null,
    ),
  }));

  return {
    title,
    tooltip,
    // Assigned by position, and only as many as there are series, so the order is walked
    // rather than cycled. A ninth series never gets here: `overSeriesLimit` refuses the
    // chart before it is built, because the ninth colour ECharts would reach for is the
    // first one again.
    color: [...SERIES.slice(0, groups.length)],
    ...legend(
      groups.filter((group): group is string => group !== undefined),
      Boolean(title),
    ),
    xAxis: {
      ...axis(x),
      ...(categorical ? { data: categories, axisLabel: categoryLabel(categories) } : {}),
    },
    yAxis: axis(y),
    series: series as SeriesOption[],
  };
}

/**
 * The key, under the header. Two or more entries or none at all: a key with one entry is
 * a label pretending to be a key, and the title already names that series.
 *
 * The entries wear ink, never the colour of the series they name. The colour is in the
 * marker beside the word, which is what carries the identity; text that takes a series
 * colour is text that fails a contrast rule to say something the marker already said.
 */
function legend(names: string[], titled: boolean) {
  if (names.length < 2) return {};
  return {
    legend: {
      data: [...names],
      top: titled ? 30 : 2,
      left: 0,
      itemWidth: 9,
      itemHeight: 9,
      itemGap: 14,
      icon: "roundRect",
      textStyle: { fontFamily: MONO, fontSize: 11, color: INK_2 },
    },
    // The plot starts below the key rather than under it. ECharts leaves no room for a
    // legend on its own and would draw the top of a bar through the words. The gap also
    // has to clear the value axis's name, which is drawn above the grid.
    grid: { top: titled ? 82 : 54, left: 8, right: 16, bottom: 8, containLabel: true },
  };
}

/** What a mark stands for. The shape ECharts hands a tooltip formatter, narrowed to the
 * three fields this one reads. */
export type Mark = { name: string; seriesName?: string; value: Value | Value[] };

/**
 * The text in a tooltip: the category, the series when a colour channel made one, and
 * the measure. Values are the ones in the result set, printed as they arrived, because a
 * tooltip that rounds is a tooltip that has to be checked somewhere else.
 */
export function markText(spec: Spec, mark: Mark): string {
  const { x, y, color } = spec.chart.encoding;
  // A time or value axis carries the pair, a category axis carries the measure alone and
  // names the category on the axis.
  const pair = Array.isArray(mark.value) ? mark.value : null;
  const line = (channel: Channel, value: Value) => `${axisName(channel)}: ${label(value)}`;

  return [
    ...(x ? [line(x, pair ? pair[0] : mark.name)] : []),
    ...(color && mark.seriesName ? [line(color, mark.seriesName)] : []),
    line(y, pair ? pair[1] : (mark.value as Value)),
  ].join("\n");
}

/** Axis chrome. Hairline gridlines a step off the surface, mono for a field name and for
 * a figure, both of which a machine produced. */
const axis = (channel: Channel) => ({
  type: AXIS_TYPE[channel.type],
  name: axisName(channel),
  nameTextStyle: { fontFamily: MONO, fontSize: 11, color: INK_3 },
  axisLine: { lineStyle: { color: RULE_2 } },
  axisTick: { lineStyle: { color: RULE_2 } },
  axisLabel: LABEL,
  splitLine: { lineStyle: { color: RULE } },
});

/**
 * Every category keeps its label. `interval: 0` emits all of them and `hideOverlap`
 * stops ECharts dropping the ones it thinks collide, so a crowded axis leans its labels
 * over instead of naming five bars out of nine.
 *
 * A label past the width is cut rather than wrapped. Wrapping was tried: a rotated label
 * on two lines crosses the ones either side of it, and a rotated label left to run its
 * full length shrinks the plot to a strip. Cut, the axis stays legible and the whole
 * value is one hover away, which is what the tooltip is for.
 */
const categoryLabel = (categories: string[]) => {
  const longest = Math.max(...categories.map((category) => category.length));
  const crowded = categories.length > ROTATE_ABOVE || longest > ROTATE_LONGER_THAN;

  return {
    ...LABEL,
    interval: 0,
    hideOverlap: false,
    rotate: crowded ? 45 : 0,
    width: 140,
    overflow: "truncate" as const,
  };
};

/** Mark geometry the design system fixes. A stacked segment stays square, because a
 * rounded top halfway up a bar reads as the top of the bar. */
function markStyle(mark: Spec["chart"]["mark"], stack?: boolean) {
  const END: [number, number, number, number] = [4, 4, 0, 0];

  if (mark === "bar") {
    return { barMaxWidth: 24, ...(stack ? {} : { itemStyle: { borderRadius: END } }) };
  }
  if (mark === "line" || mark === "area") {
    return {
      lineStyle: { width: 2 },
      symbolSize: 8,
      itemStyle: { borderColor: SURF, borderWidth: 2 },
    };
  }
  return {};
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
