import { useEffect, useMemo, useRef } from "react";
import * as echarts from "echarts";
import { buildOption, type Row, type Spec } from "./option";

export default function Chart({ spec, rows }: { spec: Spec; rows: Row[] }) {
  const host = useRef<HTMLDivElement>(null);
  const option = useMemo(() => buildOption(spec, rows), [spec, rows]);

  useEffect(() => {
    if (host.current === null || option === null) return;
    const chart = echarts.init(host.current);
    chart.setOption(option);
    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      chart.dispose();
    };
  }, [option]);

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
