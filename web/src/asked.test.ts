import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useAsked } from "./asked";
import type { Answered, Step, Suggestion } from "./api";
import type { Draft, Spec } from "./spec/spec";

/**
 * The request lifecycle, driven directly.
 *
 * This machine — text in, one request out, an outcome or a dropped answer back — used to be
 * eight closures inside `App.tsx`, and the only way to reach any of it was to render the
 * whole application: a rail, three views, two panels, a chart renderer and every request the
 * interface makes at startup. So the properties that matter most here were held by one
 * heavyweight file that mostly proves something else, and several were held nowhere at all.
 *
 * `renderHook` gives the hook a lifetime and nothing else, which is why this is a plain
 * `.test.ts` rather than one of the `.dom.test.tsx` files: there is no markup to press. What
 * is asserted is what the hook returns, because that is what the interface draws from.
 *
 * The API is stubbed, and `execute` is *held*: a test decides which request answers first,
 * which is the whole of the superseded-answer property and the one thing a clock cannot be
 * trusted to reproduce.
 */
vi.mock("./api", async () => {
  const actual = await vi.importActual<typeof import("./api")>("./api");
  return { ...actual, execute: vi.fn(), ask: vi.fn(), critique: vi.fn() };
});

const { Refused, ask, critique, execute } = await import("./api");

const spec = (title: string): Spec =>
  ({
    spec_version: "1",
    title,
    query: {
      from: "vizmith.shop.orders",
      group_by: [{ column: "vizmith.shop.orders.status", as: "status" }],
      aggregates: [{ fn: "sum", column: "vizmith.shop.orders.total", as: "revenue" }],
      limit: 500,
    },
    chart: {
      mark: "bar",
      encoding: {
        x: { field: "status", type: "nominal" },
        y: { field: "revenue", type: "quantitative" },
      },
    },
  }) as unknown as Spec;

const draft = (title: string) => spec(title) as unknown as Draft;

const COST = { calls: 3, prompt: 900, completion: 100, total: 1000 };

/** Every `execute` call, held until the test lets one of them go. Returned in the order the
 * hook made them, so a test can answer the second before the first. */
function holding() {
  const settling: { answer: (said: Answered) => void; refuse: (error: unknown) => void }[] = [];
  vi.mocked(execute).mockImplementation(
    () => new Promise((resolve, reject) => settling.push({ answer: resolve, refuse: reject })),
  );
  return settling;
}

/** The hook, with the text it is about to run already in it. `retype` is how the editor
 * writes, and every other entry point writes through the hook itself. */
function asking(text = "") {
  const held = renderHook(() => useAsked());
  if (text !== "") act(() => held.result.current.retype(text));
  return held;
}

afterEach(() => vi.clearAllMocks());

describe("running a spec", () => {
  it("draws what came back, and nothing while it is still coming", async () => {
    const settling = holding();
    const { result } = asking(JSON.stringify(spec("Revenue")));

    act(() => result.current.run());
    expect(result.current.running).toBe(true);
    expect(result.current.outcome.kind).toBe("nothing");

    await act(async () => settling[0].answer({ spec: spec("Revenue"), rows: [{ revenue: 1 }] }));
    expect(result.current.running).toBe(false);
    expect(result.current.outcome).toMatchObject({ kind: "chart", rows: [{ revenue: 1 }] });
  });

  it("refuses text that is not JSON without sending it anywhere", () => {
    holding();
    const { result } = asking("{ not a spec");

    act(() => result.current.run());

    expect(execute).not.toHaveBeenCalled();
    expect(result.current.outcome).toMatchObject({ kind: "refused", heading: "What the parser said" });
  });

  it("says what the server refused, in the words the server used", async () => {
    const settling = holding();
    const { result } = asking(JSON.stringify(spec("Revenue")));

    act(() => result.current.run());
    await act(async () =>
      settling[0].refuse(new Refused(["'limit' is a required property"], { said: true })),
    );

    expect(result.current.outcome).toMatchObject({
      kind: "refused",
      heading: "What the validator said",
      lines: ["'limit' is a required property"],
    });
    expect(result.current.running).toBe(false);
  });
});

describe("two runs in flight at once", () => {
  /** The property `runs.ts` exists for, held against the thing that consults it. A warehouse
   * does not answer in the order it was asked, and what a late answer would leave on screen
   * is the second spec in the editor beside the chart drawn from the first. */
  it("draws the answer to the question being waited for, whichever arrives first", async () => {
    const settling = holding();
    const { result } = asking();

    act(() => result.current.edited(draft("first")));
    act(() => result.current.edited(draft("second")));
    expect(settling).toHaveLength(2);

    await act(async () => settling[1].answer({ spec: spec("second"), rows: [{ revenue: 2 }] }));
    await act(async () => settling[0].answer({ spec: spec("first"), rows: [{ revenue: 1 }] }));

    expect(result.current.outcome).toMatchObject({ kind: "chart", rows: [{ revenue: 2 }] });
  });

  it("keeps waiting when the superseded one finishes, and stops when the awaited one does", async () => {
    const settling = holding();
    const { result } = asking();

    act(() => result.current.edited(draft("first")));
    act(() => result.current.edited(draft("second")));

    await act(async () => settling[0].answer({ spec: spec("first"), rows: [] }));
    expect(result.current.running).toBe(true);

    await act(async () => settling[1].answer({ spec: spec("second"), rows: [] }));
    expect(result.current.running).toBe(false);
  });

  it("drops a superseded refusal too, so a dead request cannot refuse a live chart", async () => {
    const settling = holding();
    const { result } = asking();

    act(() => result.current.edited(draft("first")));
    act(() => result.current.edited(draft("second")));

    await act(async () => settling[1].answer({ spec: spec("second"), rows: [] }));
    await act(async () => settling[0].refuse(new Refused(["gone"], { said: true })));

    expect(result.current.outcome.kind).toBe("chart");
  });
});

describe("a well that was edited", () => {
  /** One dimension and no measure is what every drag but the last produces. It is not a
   * spec that failed, and answering it with a required property error would put a refusal
   * on screen for every drop on the way to a chart. */
  it("writes what was built even when it is not finished, and runs nothing", () => {
    holding();
    const { result } = asking();
    const unfinished = draft("no measure");
    unfinished.query = { ...unfinished.query, aggregates: [] };
    unfinished.chart = { ...unfinished.chart, encoding: { x: unfinished.chart.encoding.x } };

    act(() => result.current.edited(unfinished));

    expect(JSON.parse(result.current.text).title).toBe("no measure");
    expect(execute).not.toHaveBeenCalled();
  });

  it("runs one it can draw, so a chart appears on the drop that finishes it", () => {
    holding();
    const { result } = asking();

    act(() => result.current.edited(draft("finished")));

    expect(execute).toHaveBeenCalledTimes(1);
  });
});

describe("asking a question", () => {
  it("sends nothing for a question that is only spaces", () => {
    const { result } = asking();

    act(() => result.current.askQuestion("   "));

    expect(ask).not.toHaveBeenCalled();
    expect(result.current.running).toBe(false);
  });

  /** The wait is not one wait: #119 made the server say which step it is on, and this is
   * the state the canvas and the live region are both drawn from. */
  it("writes down the step the server last reported", async () => {
    let report!: (step: Step) => void;
    vi.mocked(ask).mockImplementation(
      (_question, watching) =>
        new Promise(() => {
          report = watching!;
        }),
    );
    const { result } = asking();

    act(() => result.current.askQuestion("revenue per country"));
    expect(result.current.working).toEqual({ asking: true, step: null });

    act(() => report({ step: "model", attempt: 2, of: 3 }));
    expect(result.current.working).toEqual({
      asking: true,
      step: { step: "model", attempt: 2, of: 3 },
    });
  });

  it("replaces the editor with the spec that came back, since the model wrote it", async () => {
    vi.mocked(ask).mockResolvedValue({ spec: spec("Revenue per status"), rows: [], cost: COST });
    const { result } = asking(JSON.stringify(spec("what was there before")));

    await act(async () => result.current.askQuestion("revenue per status"));

    expect(JSON.parse(result.current.text).title).toBe("Revenue per status");
    expect(result.current.spent).toEqual({ cost: COST, what: "this question" });
  });

  /** The expensive failure is the one that took three attempts and produced nothing, and
   * a refusal that hid its cost is the one case where the number is most worth having. */
  it("reports what a refusal cost, not only what an answer cost", async () => {
    vi.mocked(ask).mockRejectedValue(new Refused(["no spec validated"], { said: true, cost: COST }));
    const { result } = asking();

    await act(async () => result.current.askQuestion("something impossible"));

    expect(result.current.outcome.kind).toBe("refused");
    expect(result.current.spent).toEqual({ cost: COST, what: "this question" });
  });

  it("clears the cost when a spec is run by hand, because that reached no model", async () => {
    vi.mocked(ask).mockResolvedValue({ spec: spec("asked"), rows: [], cost: COST });
    const settling = holding();
    const { result } = asking();

    await act(async () => result.current.askQuestion("revenue per status"));
    expect(result.current.spent).not.toBeNull();

    act(() => result.current.run());
    await act(async () => settling[0].answer({ spec: spec("asked"), rows: [] }));

    expect(result.current.spent).toBeNull();
  });
});

describe("the way back", () => {
  it("is nothing at all until something has replaced a chart", () => {
    expect(asking().result.current.back).toBeNull();
  });

  it("gives back the chart a drill replaced, and the text that drew it", async () => {
    const settling = holding();
    const { result } = asking(JSON.stringify(spec("Revenue")));

    act(() => result.current.run());
    await act(async () => settling[0].answer({ spec: spec("Revenue"), rows: [{ revenue: 1 }] }));

    act(() => result.current.drilled(spec("Revenue in Germany")));
    await act(async () => settling[1].answer({ spec: spec("Revenue in Germany"), rows: [] }));
    expect(result.current.outcome).toMatchObject({ kind: "chart", rows: [] });

    act(() => result.current.back!());

    expect(result.current.outcome).toMatchObject({ kind: "chart", rows: [{ revenue: 1 }] });
    expect(JSON.parse(result.current.text).title).toBe("Revenue");
    expect(result.current.back).toBeNull();
  });

  /** Every entry keeps the result set it replaced, so the stack is bounded rather than one
   * held copy of every chart drilled through for as long as the tab is open. */
  it("keeps ten steps and drops the oldest, since it is walked from the newest end", async () => {
    const settling = holding();
    const { result } = asking(JSON.stringify(spec("step 0")));

    for (let step = 1; step <= 12; step += 1) {
      act(() => result.current.drilled(spec(`step ${step}`)));
      await act(async () => settling[step - 1].answer({ spec: spec(`step ${step}`), rows: [] }));
    }

    let steps = 0;
    while (result.current.back !== null) {
      act(() => result.current.back!());
      steps += 1;
    }

    expect(steps).toBe(10);
    expect(JSON.parse(result.current.text).title).toBe("step 2");
  });

  /** A tile opened for correction has a way back already — the tile it came from — so it
   * does not push one, and the chart it replaced was not anybody's to return to. */
  it("is not written by a tile opened for correction", async () => {
    const settling = holding();
    const { result } = asking();

    act(() => result.current.open(spec("a tile")));
    await act(async () => settling[0].answer({ spec: spec("a tile"), rows: [] }));

    expect(result.current.back).toBeNull();
    expect(JSON.parse(result.current.text).title).toBe("a tile");
  });
});

describe("the second opinion", () => {
  const said: Suggestion = { findings: ["A bar chart over a date is a line."], spec: spec("Better"), errors: [] };

  const charted = async () => {
    const settling = holding();
    const held = asking(JSON.stringify(spec("Revenue")));
    act(() => held.result.current.run());
    await act(async () => settling[0].answer({ spec: spec("Revenue"), rows: [{ revenue: 1 }] }));
    return { ...held, settling };
  };

  it("asks about the chart that is drawn, not about what is in the editor", async () => {
    vi.mocked(critique).mockResolvedValue(said);
    const { result } = await charted();

    act(() => result.current.retype(JSON.stringify(spec("half typed"))));
    await act(async () => await result.current.suggest());

    expect(vi.mocked(critique).mock.calls[0][0]).toMatchObject({ title: "Revenue" });
    expect(result.current.suggestion).toEqual(said);
  });

  it("says nothing where there is no chart, because a finding is about one that exists", async () => {
    const { result } = asking();

    await act(async () => await result.current.suggest());

    expect(critique).not.toHaveBeenCalled();
    expect(result.current.suggestion).toBeNull();
  });

  it("shows a failed request in the shape a suggestion arrives in", async () => {
    vi.mocked(critique).mockRejectedValue(new Refused(["the endpoint never answered"], { said: true }));
    const { result } = await charted();

    await act(async () => await result.current.suggest());

    expect(result.current.suggestion).toEqual({
      findings: [],
      spec: null,
      errors: ["the endpoint never answered"],
    });
    expect(result.current.suggesting).toBe(false);
  });

  /** A finding is about the spec that was sent. The next chart is a different spec, so a
   * finding left hanging over it would be an opinion about something else. */
  it("goes when the next run starts", async () => {
    vi.mocked(critique).mockResolvedValue(said);
    const { result, settling } = await charted();

    await act(async () => await result.current.suggest());
    expect(result.current.suggestion).not.toBeNull();

    act(() => result.current.edited(draft("something else")));
    expect(result.current.suggestion).toBeNull();
    await act(async () => settling[1].answer({ spec: spec("something else"), rows: [] }));
  });

  it("keeps the chart it replaces when it is taken", async () => {
    vi.mocked(critique).mockResolvedValue(said);
    const { result, settling } = await charted();

    await act(async () => await result.current.suggest());
    act(() => result.current.takeSuggestion());
    await act(async () => settling[1].answer({ spec: spec("Better"), rows: [] }));

    expect(JSON.parse(result.current.text).title).toBe("Better");
    expect(result.current.back).not.toBeNull();
  });

  it("does nothing when there is no spec to take", async () => {
    vi.mocked(critique).mockResolvedValue({ findings: ["Nothing survives the rules."], spec: null, errors: [] });
    const { result } = await charted();

    await act(async () => await result.current.suggest());
    act(() => result.current.takeSuggestion());

    expect(execute).toHaveBeenCalledTimes(1);
  });
});
