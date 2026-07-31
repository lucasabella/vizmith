import { useEffect, useMemo, useRef } from "react";
import * as echarts from "echarts";
import { buildOption, label, type Row, type Spec, type Value } from "./option";
import type { Clicked } from "../spec/drill";

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
  const option = useMemo(() => buildOption(spec, rows), [spec, rows]);
  // The handler is read through a ref so that a new one does not tear the chart down and
  // rebuild it. Only the option is worth re-initialising for.
  const select = useRef(onSelect);
  select.current = onSelect;

  useEffect(() => {
    if (host.current === null || option === null) return;
    const chart = echarts.init(host.current);
    chart.setOption(option);
    // A click carries what the renderer drew: the category on the axis and the series
    // name where a colour channel made one. Both are labels, and what they stand for is
    // looked up in the result set rather than parsed back out of them.
    chart.on("click", (params: { name?: string; seriesName?: string; value?: unknown }) => {
      if (select.current === undefined) return;
      const pair = Array.isArray(params.value) ? (params.value as Value[]) : null;
      const category = pair ? pair[0] : (params.name ?? null);
      select.current({
        category,
        series: spec.chart.encoding.color ? params.seriesName : undefined,
      });
    });
    // The window is not what changes size. Collapsing a panel widens the canvas without
    // the window moving, and that is the gesture that exists to make room for a chart.
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(host.current);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, [option, spec.chart.encoding.color]);

  // A question with no dimension. The validator has already established that the query
  // returns one row, so the measure is read off it and drawn as a figure. There is no
  // option to build for that, which is why this is read off the encoding.
  const { x, y } = spec.chart.encoding;
  if (x === undefined && rows.length > 0) {
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
