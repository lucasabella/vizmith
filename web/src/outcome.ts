import { overSeriesLimit, type Row, type Spec } from "./chart/option";
import { counted } from "./counted";

/** Which part refused, as the server named it. It is the only thing that can: a question
 * passes through the source, the model and the source again, and from the browser they are
 * one request. */
export type Spoke = "source" | "model" | "spec";

/**
 * What the canvas is showing. A refusal carries the machine's own words and a sentence
 * that says what they mean, because one without the other is either unreadable or
 * unverifiable.
 */
export type Outcome =
  | { kind: "nothing" }
  | { kind: "chart"; spec: Spec; rows: Row[] }
  | { kind: "refused"; heading: string; lines: string[]; plain: string; spoke?: Spoke };

/** What is in flight. Not an Outcome: running is the absence of one so far. */
export type Working = "question" | "spec" | null;

export const REJECTED =
  "The spec did not pass validation, so nothing ran against the source. Correct what is named above and run it again.";

export const SAID: Record<Spoke, { heading: string; plain: string }> = {
  source: {
    heading: "What the source said",
    plain:
      "The spec passed validation and the source refused the statement it compiled to. Change the query or check the source.",
  },
  model: {
    heading: "What the model said",
    plain:
      "The model endpoint never answered, so no spec was written. Check the endpoint and the key, then ask again.",
  },
  spec: {
    heading: "What the spec check said",
    plain:
      "The spec passed validation, and this rule runs after it because it needs the compiled query, so nothing ran against the source. Correct what is named above and run it again.",
  },
};

/**
 * What the canvas has become, in one sentence, for the live region that announces it.
 *
 * Every message this interface produces appears by being swapped into the tree, which a
 * screen reader does not report. That covers most of what the product says: the wait, the
 * chart, and the refusal with the validator's or the source's own words in it.
 *
 * One region carries the outcome and it is polite, because the canvas changes on every drop
 * into a well and a region that says too much is worse than none. It says what the canvas
 * now shows rather than narrating the way there, which is why a chart is its row count and
 * a refusal is its heading and its first line — the full list is on screen, and reading a
 * list of validator errors aloud on every keystroke of a drag is how a person turns this
 * off. The badge in the strip stays silent for the same reason: it is a second account of
 * this outcome, and two regions announcing one thing is the noise the mitigation is about.
 */
export function announced(outcome: Outcome, working: Working): string {
  if (working !== null) {
    return working === "question" ? "Answering the question." : "Running the spec.";
  }
  if (outcome.kind === "refused") {
    return [outcome.heading, outcome.lines[0]].filter(Boolean).join(": ");
  }
  if (outcome.kind !== "chart") return "No chart yet.";

  // A 200 is not a chart. Two things the renderer refuses after the server has answered,
  // and both are what the canvas draws instead — announcing "a chart of 10 rows" over the
  // top of "there are eight colours to tell them apart with" is the region reporting a
  // request rather than a screen. The predicates are the renderer's own, imported rather
  // than restated, because a second copy is one that can disagree with what was drawn.
  const tooMany = overSeriesLimit(outcome.spec, outcome.rows);
  if (tooMany !== null) return `What the renderer said: ${tooMany}`;
  if (outcome.rows.length === 0) return "No rows to draw.";
  // The one shape that is not a chart either. The grammar answers a question with no
  // dimension as a figure, and calling it a row count is what the meta line used to do.
  // `Chart.tsx` decides it the same way: no `x`, and something to read off.
  if (outcome.spec.chart.encoding.x === undefined) return "One figure.";
  return `A chart of ${counted(outcome.rows.length, "row")}.`;
}
