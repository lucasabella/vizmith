import { describe, expect, it } from "vitest";
import type { Spec } from "./chart/option";
import { announced, REJECTED, SAID, type Outcome } from "./outcome";

const spec = (encoding: Spec["chart"]["encoding"]): Spec =>
  ({ chart: { mark: "bar", encoding } }) as Spec;

const bar = spec({
  x: { field: "country", type: "nominal" },
  y: { field: "revenue", type: "quantitative" },
});
const figure = spec({ y: { field: "revenue", type: "quantitative" } });

const rows = (many: number) => Array.from({ length: many }, (_, at) => ({ revenue: at }));

const chart = (many: number, of: Spec = bar): Outcome => ({ kind: "chart", spec: of, rows: rows(many) });

describe("what the canvas announces", () => {
  it("says which wait is being waited, because that is the only signal during one", () => {
    expect(announced({ kind: "nothing" }, "question")).toBe("Answering the question.");
    expect(announced({ kind: "nothing" }, "spec")).toBe("Running the spec.");
  });

  it("says what is in flight rather than what is still on screen", () => {
    // The chart under the spinner answered the previous question. Announcing it while the
    // next one is in flight is announcing the wrong answer.
    expect(announced(chart(30), "question")).toBe("Answering the question.");
  });

  it("says the row count when a chart lands, since that is what changed", () => {
    expect(announced(chart(30), null)).toBe("A chart of 30 rows.");
    expect(announced(chart(1), null)).toBe("A chart of 1 row.");
  });

  it("calls the one shape that is not a chart what it is", () => {
    // No dimension, one row. The grammar treats this specially and so does the drawing;
    // "a chart of 1 row" is the description this whole shape exists to avoid.
    expect(announced(chart(1, figure), null)).toBe("One figure.");
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
