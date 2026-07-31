import { useEffect, useMemo, useRef } from "react";
import * as echarts from "echarts";
import { buildOption, label, type Row, type Spec } from "./option";

export default function Chart({ spec, rows }: { spec: Spec; rows: Row[] }) {
  const host = useRef<HTMLDivElement>(null);
  const option = useMemo(() => buildOption(spec, rows), [spec, rows]);

  useEffect(() => {
    if (host.current === null || option === null) return;
    const chart = echarts.init(host.current);
    chart.setOption(option);
    // The window is not what changes size. Collapsing a panel widens the canvas without
    // the window moving, and that is the gesture that exists to make room for a chart.
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(host.current);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, [option]);

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
