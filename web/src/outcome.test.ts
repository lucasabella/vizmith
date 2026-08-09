import { describe, expect, it } from "vitest";
import { Refused } from "./api";
import { SERIES_LIMIT } from "./chart/option";
import type { Spec } from "./spec/spec";
import { announced, refusal, REJECTED, SAID, STEP, waiting, type Outcome } from "./outcome";
import { type StepName } from "./api";

const spec = (encoding: Spec["chart"]["encoding"]): Spec =>
  ({ chart: { mark: "bar", encoding } }) as Spec;

const bar = spec({
  x: { field: "country", type: "nominal" },
  y: { field: "revenue", type: "quantitative" },
});
const figure = spec({ y: { field: "revenue", type: "quantitative" } });

const rows = (many: number) => Array.from({ length: many }, (_, at) => ({ revenue: at }));

const chart = (many: number, of: Spec = bar): Outcome => ({ kind: "chart", spec: of, rows: rows(many) });

const asked = { asking: true, step: null };
const stepped = (step: StepName, attempt = 0, of = 0) => ({ asking: true, step: { step, attempt, of } });

describe("what the canvas announces", () => {
  it("says which wait is being waited, because that is the only signal during one", () => {
    expect(announced({ kind: "nothing" }, asked)).toBe("Answering the question.");
    expect(announced({ kind: "nothing" }, { asking: false, step: null })).toBe("Running the spec.");
  });

  it("says which step, because most of a question's wait is not the model", () => {
    // Three announcements in one question rather than one. That is the point: somebody who
    // cannot see the canvas is the person a blank wait is worst for.
    expect(announced({ kind: "nothing" }, stepped("profiles"))).toBe("Reading the schema.");
    expect(announced({ kind: "nothing" }, stepped("query"))).toBe("Running the query.");
  });

  it("says which attempt, since the third costs three times what the first did", () => {
    expect(announced({ kind: "nothing" }, stepped("model", 1, 3))).toBe("Asking the model.");
    expect(announced({ kind: "nothing" }, stepped("model", 3, 3))).toBe(
      "Asking the model, attempt 3 of 3.",
    );
  });

  it("says what is in flight rather than what is still on screen", () => {
    // The chart under the spinner answered the previous question. Announcing it while the
    // next one is in flight is announcing the wrong answer.
    expect(announced(chart(30), asked)).toBe("Answering the question.");
  });

  it("says the row count when a chart lands, since that is what changed", () => {
    expect(announced(chart(30), null)).toBe("A chart of 30 rows.");
    expect(announced(chart(1), null)).toBe("A chart of 1 row.");
  });

  it("calls the one shape that is not a chart what it is", () => {
    // No dimension. The grammar treats this specially and so does the drawing; "a chart of
    // 1 row" is the description this whole shape exists to avoid. The predicate is
    // `Chart.tsx`'s — no `x`, and something to read off — rather than a second one that
    // can disagree with what was drawn.
    expect(announced(chart(1, figure), null)).toBe("One figure.");
    expect(announced(chart(3, figure), null)).toBe("One figure.");
  });

  it("says a query answered nothing rather than calling it a chart of no rows", () => {
    // A 200 with an empty result set draws "No rows to draw", and announcing "a chart of 0
    // rows" over the top of it is the region reporting a request rather than a screen.
    expect(announced(chart(0), null)).toBe("No rows to draw.");
    expect(announced(chart(0, figure), null)).toBe("No rows to draw.");
  });

  it("says what the renderer refused, which is also drawn instead of a chart", () => {
    const coloured = spec({
      x: { field: "country", type: "nominal" },
      y: { field: "revenue", type: "quantitative" },
      color: { field: "category", type: "nominal" },
    });
    const many: Outcome = {
      kind: "chart",
      spec: coloured,
      rows: Array.from({ length: SERIES_LIMIT + 2 }, (_, at) => ({ category: `c${at}` })),
    };

    const said = announced(many, null);
    expect(said).toContain("What the renderer said:");
    expect(said).toContain(`${SERIES_LIMIT} colours`);
  });

  it("says which part refused and its first line, not the whole list", () => {
    const refused: Outcome = {
      kind: "refused",
      spoke: "source",
      lines: ["TABLE_OR_VIEW_NOT_FOUND", "and four more things"],
      ...SAID.source,
    };

    expect(announced(refused, null)).toBe("What the source said: TABLE_OR_VIEW_NOT_FOUND");
  });

  it("announces a validator refusal the same way, with what it said first", () => {
    const rejected: Outcome = {
      kind: "refused",
      heading: "What the validator said",
      lines: ["'limit' is a required property"],
      plain: REJECTED,
    };

    expect(announced(rejected, null)).toBe(
      "What the validator said: 'limit' is a required property",
    );
  });

  it("still says something for a refusal that named nothing", () => {
    const bare: Outcome = { kind: "refused", heading: "What the server said", lines: [], plain: "" };

    expect(announced(bare, null)).toBe("What the server said");
  });

  it("says there is nothing yet rather than going quiet", () => {
    expect(announced({ kind: "nothing" }, null)).toBe("No chart yet.");
  });
});

describe("reading a failure, once, for everything that catches one", () => {
  // The canvas and a dashboard tile call the same endpoint and used to disagree about what
  // a refusal is: the canvas showed the heading and the whole list, the tile showed the
  // first line under "What the source said" whether or not a source was involved. These
  // are that one reading, and every caller now goes through it.

  it("takes the heading and the sentence from the part the server named", () => {
    const read = refusal(new Refused(["TABLE_OR_VIEW_NOT_FOUND"], { spoke: "source" }));

    expect(read).toEqual({
      kind: "refused",
      spoke: "source",
      lines: ["TABLE_OR_VIEW_NOT_FOUND"],
      ...SAID.source,
    });
  });

  it("does not send anybody to the source for a refusal no source saw", () => {
    // The whole reason `spoke` exists. A rationed request touched neither endpoint, and a
    // tile that headed it "What the source said" sent a person to a warehouse that was
    // never asked anything.
    const read = refusal(new Refused(["Wait 12 seconds"], { spoke: "rations" }));

    expect(read.heading).toBe(SAID.rations.heading);
    expect(read.plain).toContain("Nothing was asked of the model or the source");
  });

  it("calls a 400 with a list of problems the validator, because that is what it is", () => {
    const read = refusal(new Refused(["'limit' is a required property"]));

    expect(read.heading).toBe("What the validator said");
    expect(read.lines).toEqual(["'limit' is a required property"]);
    expect(read.plain).toBe(REJECTED);
    expect(read.spoke).toBeUndefined();
  });

  it("separates a server that failed from a validator with nothing to say", () => {
    // A 500 with no body is not a rejected spec. Telling somebody to correct what is named
    // above, when the only thing named above is a status line, is worse than saying there
    // is nothing here to act on.
    const read = refusal(new Refused(["500 Internal Server Error"], { said: false }));

    expect(read.heading).toBe("What the server said");
    expect(read.lines).toEqual(["500 Internal Server Error"]);
    expect(read.plain).not.toBe(REJECTED);
    expect(read.plain).toContain("without saying what failed");
  });

  it("says so when the request never got out of the browser", () => {
    // `fetch` rejecting is a different failure to any of the above: no server formed an
    // opinion, so quoting one would be inventing it.
    const read = refusal(new TypeError("Failed to fetch"));

    expect(read.heading).toBe("What the browser said");
    expect(read.lines).toEqual(["Failed to fetch"]);
    expect(read.plain).toBe("The request never reached the server.");
  });
});

describe("a refusal the server itself made", () => {
  it("is announced as the server's own limit rather than as the source's", () => {
    // A rationed request reached neither the model nor the warehouse, so the sentence
    // beside it must not send somebody to check one. The server names which part refused;
    // this is the browser holding a message for that name.
    const said = SAID.rations;

    expect(
      announced(
        { kind: "refused", spoke: "rations", lines: ["That is more than 20 model requests in a minute"], ...said },
        null,
      ),
    ).toBe("What this server would not spend: That is more than 20 model requests in a minute");
    expect(said.plain).toContain("Nothing was asked of the model or the source");
  });
});

describe("what the wait says it is doing", () => {
  it("names the step, because a question is not one wait", () => {
    // Measured at 152 tables: around 18 seconds of metadata before a token is requested.
    // A spinner over all of it cannot say whether the model is slow or the warehouse is cold.
    expect(waiting(stepped("profiles")).title).toBe("Reading the schema");
    expect(waiting(stepped("model", 1, 3)).title).toBe("Asking the model");
    expect(waiting(stepped("query")).title).toBe("Running the query");
  });

  it("counts the attempt only once there has been more than one", () => {
    // The first attempt is the expected case and saying "attempt 1 of 3" over it reads as a
    // warning about something that has not happened.
    expect(waiting(stepped("model", 1, 3)).title).not.toContain("attempt");
    expect(waiting(stepped("model", 2, 3)).title).toBe("Asking the model, attempt 2 of 3");
    expect(waiting(stepped("model", 2, 3)).body).toBe(STEP.model.body);
  });

  it("has words for the moment before the first step arrives", () => {
    // One round trip with the request gone and nothing back, and the same moment for a
    // server that answered with a body rather than a stream.
    expect(waiting(asked).title).toBe("Answering the question");
    expect(waiting(asked).body).toBe(STEP.profiles.body);
  });

  it("says the source and not the model for a spec that was run by hand", () => {
    const running = waiting({ asking: false, step: null });

    expect(running.title).toBe("Running the spec");
    expect(running.body).toContain("nowhere near the model");
  });
});
