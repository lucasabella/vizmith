import { Suspense, lazy } from "react";
import type { Row, Spec } from "./option";
import type { Clicked } from "../spec/drill";

/**
 * The renderer, fetched when something needs one drawn.
 *
 * ECharts is 70% of what this interface ships — 562 kB of the 812 kB build, 192 kB of the
 * 270 kB gzipped — and `Chart.tsx` is already careful about that: it registers four series
 * types and four components off `echarts/core` rather than pulling the barrel, which is
 * 1.37 MB. There is not much left to trim. What is left is *when* it arrives.
 *
 * Nothing on the first paint draws a chart. The shell, the Fields panel, the empty state
 * and the Data view need none of it, and somebody who opens the app to paste a spec has not
 * drawn one yet either. Behind this boundary the first paint is about 76 kB gzipped instead
 * of 269 kB, and the rest lands while a person is reading the panel.
 *
 * `option.ts` stays in the first chunk on purpose. It imports ECharts for its *types* only,
 * so it costs nothing at runtime, and `overSeriesLimit` is what decides a chart is refused
 * before one is drawn — a refusal that had to fetch a renderer to say a renderer will not
 * draw this would be the wrong way round.
 *
 * Where it does not help, and this is worth saying rather than discovering: opening a saved
 * dashboard draws a chart immediately, so that path pays the fetch anyway. It pays it after
 * the first paint rather than before it, which is better and is not free.
 */
const Chart = lazy(() => import("./Chart"));

export default function Deferred(props: {
  spec: Spec;
  rows: Row[];
  onSelect?: (clicked: Clicked) => void;
}) {
  return (
    <Suspense fallback={<Fetching />}>
      <Chart {...props} />
    </Suspense>
  );
}

/**
 * What is on screen for the one moment the renderer is in flight.
 *
 * The same shape the tile and the canvas already use for work in progress, because it is
 * the same statement: something is happening and there is nothing to read yet. It says the
 * renderer rather than the query, since the query is already answered by the time anything
 * reaches here — telling somebody their query is running when it has finished is the kind
 * of small lie that makes a progress message worth nothing.
 */
function Fetching() {
  return <p className="grid__working">Fetching the renderer.</p>;
}
