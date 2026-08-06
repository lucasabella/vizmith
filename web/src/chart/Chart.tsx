import { useEffect, useMemo, useRef } from "react";
import { BarChart, LineChart, PieChart, ScatterChart } from "echarts/charts";
import {
  GridComponent,
  LegendComponent,
  TitleComponent,
  TooltipComponent,
} from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { buildOption, clickedValue, label, type Row, type Spec } from "./option";
import type { Clicked } from "../spec/drill";

/**
 * What the renderer draws, and nothing else. The `echarts` barrel registers every chart
 * type, component and coordinate system the library ships, which is 1.37 MB of JavaScript
 * for five marks — and it travels inside the wheel, so every install carries it.
 *
 * The four series types are what the grammar's marks compile to (`SERIES_TYPE` and the arc
 * in `option.ts`) and the four components are what the built options carry. The mark set is
 * closed by the schema, so this cannot quietly need a fifth without the grammar growing
 * first. A component that is used and not registered draws nothing rather than raising,
 * which is why every mark has a test that builds its option and the browser suite paints
 * one.
 */
echarts.use([
  BarChart,
  LineChart,
  ScatterChart,
  PieChart,
  TitleComponent,
  TooltipComponent,
  LegendComponent,
  GridComponent,
  CanvasRenderer,
]);

export default function Chart({
  spec,
  rows,
  onSelect,
}: {
  spec: Spec;
  rows: Row[];
  onSelect?: (clicked: Clicked) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);
  const option = useMemo(() => buildOption(spec, rows), [spec, rows]);
  // What a click means, read through a ref so that a new handler, a new spec or a new
  // result set does not tear the chart down and rebuild it.
  const clicking = useRef({ onSelect, spec, rows });
  clicking.current = { onSelect, spec, rows };

  // A question with no dimension. The validator has already established that the query
  // returns one row, so the measure is read off it and drawn as a figure. There is no
  // option to build for that, which is why this is read off the encoding.
  const { x, y } = spec.chart.encoding;
  const figure = x === undefined && rows.length > 0;
  // Whether the canvas is the thing on screen. The figure and the empty state are other
  // elements, so the host goes away and the instance has to go with it: an instance kept
  // over a detached element is a chart nobody can see and a canvas nobody frees.
  const drawing = option !== null && !figure;

  // One instance for as long as the canvas is mounted. Initialising per option was the
  // whole lifecycle — canvas, renderer, click handler, resize observer — paid for what is
  // a data change, on every drop into a well and every tile of a dashboard.
  useEffect(() => {
    if (!drawing || host.current === null) return;
    const instance = echarts.init(host.current);
    chart.current = instance;
    // A click carries what the renderer drew: the category on the axis and the series
    // name where a colour channel made one. Both are labels, and what they stand for is
    // looked up in the result set rather than parsed back out of them. A time axis is
    // drawn from instants rather than from labels, so that lookup is where the value the
    // source sent comes back.
    instance.on("click", (params: { name?: string; seriesName?: string; value?: unknown }) => {
      const { onSelect: selected, spec: drawn, rows: shown } = clicking.current;
      if (selected === undefined) return;
      selected({
        category: clickedValue(drawn, shown, params),
        series: drawn.chart.encoding.color ? params.seriesName : undefined,
      });
    });
    // The window is not what changes size. Collapsing a panel widens the canvas without
    // the window moving, and that is the gesture that exists to make room for a chart.
    const observer = new ResizeObserver(() => instance.resize());
    observer.observe(host.current);
    return () => {
      observer.disconnect();
      instance.dispose();
      chart.current = null;
    };
  }, [drawing]);

  // The new option replaces the one the instance holds. `setOption` merges by default, and
  // a merge is wrong here: going from a chart with a colour channel to one without would
  // keep the old series and the old legend. What this file builds is complete every time.
  useEffect(() => {
    if (chart.current === null || option === null) return;
    chart.current.setOption(option, { notMerge: true });
  }, [option]);

  if (figure) {
    return (
      <div className="figure">
        <div>
          <p className="figure__name">{spec.title ?? y.title ?? y.field}</p>
          <p className="figure__value">{label(rows[0][y.field])}</p>
        </div>
      </div>
    );
  }

  if (option === null) {
    return (
      <div className="empty">
        <div>
          <p className="empty__title">No rows to draw</p>
          <p className="empty__body">
            The query ran and returned nothing. Widen a filter or raise the row cap, then ask again.
          </p>
        </div>
      </div>
    );
  }

  return <div className="chart" ref={host} />;
}
