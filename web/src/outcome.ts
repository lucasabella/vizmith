import { Refused, type Spoke, type Step, type StepName } from "./api";
import { overSeriesLimit, type Row } from "./chart/option";
import type { Spec } from "./spec/spec";
import { counted } from "./counted";

/** Which part refused, as the server named it. Declared in `api.ts`, because it is the
 * server's own field; what each one means to a person is `SAID` below, which is this
 * interface's opinion and is the half that belongs here. */
export type { Spoke };

/**
 * What the canvas is showing. A refusal carries the machine's own words and a sentence
 * that says what they mean, because one without the other is either unreadable or
 * unverifiable.
 */
export type Outcome =
  | { kind: "nothing" }
  | { kind: "chart"; spec: Spec; rows: Row[] }
  | { kind: "refused"; heading: string; lines: string[]; plain: string; spoke?: Spoke };

/** A refusal on its own, for the places that hold one without holding a canvas. A dashboard
 * tile is either drawing or refusing; there is no "nothing" for it, because a tile that has
 * not answered yet is running its spec. */
export type Refusal = Extract<Outcome, { kind: "refused" }>;

/**
 * What is in flight. Not an Outcome: running is the absence of one so far.
 *
 * A question carries the step the server last reported, because a question is not one wait:
 * it reads the profiles, asks the model up to three times and then runs the query, and on a
 * large schema the part in front of the model is the long one. `step` is null between the
 * request leaving and the first event arriving, and for a server that answered with a body
 * rather than a stream — so the generic sentence is still written and still shown.
 */
export type Working = null | { asking: boolean; step: Step | null };

/**
 * What each step says to somebody waiting for it.
 *
 * The server names the step and this writes the sentence, which is the line `SAID` above
 * draws for `spoke`. Two of the three name a cost the person is paying and can do something
 * about — a schema nobody has profiled, a model being asked again because the last answer
 * did not validate — because a wait that explains itself is the whole of #119.
 */
export const STEP: Record<StepName, { title: string; body: string }> = {
  profiles: {
    title: "Reading the schema",
    body: "Every column is profiled before the model is asked anything, and a warehouse that was idle has to start before any of it runs. A profile is kept until the table it describes changes, so the next question skips it, and so does the next restart.",
  },
  model: {
    title: "Asking the model",
    body: "The model is being sent the profiles of the tables this question is about, and the question. No row from any table goes with them.",
  },
  query: {
    title: "Running the query",
    body: "The model has written a spec and it passed validation. The source is running the query it compiled to, and the rows come back to the chart and go nowhere near the model.",
  },
};

/** The wait, in one line: the step, and which attempt where there is one. "Asking the model"
 * for the third time is a different thing to be waiting through than the first, and it is
 * the difference between a question that cost one billed request and one that cost three. */
export function waiting(working: NonNullable<Working>): { title: string; body: string } {
  if (!working.asking) {
    return {
      title: "Running the spec",
      body: "The source is running the query. The rows come back to the chart and go nowhere near the model.",
    };
  }
  if (working.step === null) {
    return {
      title: "Answering the question",
      body: STEP.profiles.body,
    };
  }
  const said = STEP[working.step.step];
  const { attempt, of } = working.step;
  return attempt > 1 ? { ...said, title: `${said.title}, attempt ${attempt} of ${of}` } : said;
}

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
  rations: {
    // The one refusal that is Vizmith's own. Nothing was asked of the model or the source,
    // so a person reading "what the source said" would go and check a warehouse that was
    // never touched. The server's message names the ceiling and the variable that moves it.
    heading: "What this server would not spend",
    plain:
      "Nothing was asked of the model or the source. This is Vizmith's own limit on how often the endpoints that cost money may be used, so wait the moment it names, or raise the ceiling it names.",
  },
  spec: {
    heading: "What the spec check said",
    plain:
      "The spec passed validation, and this rule runs after it because it needs the compiled query, so nothing ran against the source. Correct what is named above and run it again.",
  },
};

/**
 * A refusal, turned into what the canvas shows: the machine's own words, and a sentence
 * saying what they mean.
 *
 * This is the half of a failure that belongs to this interface. The transport is `api.ts`,
 * which throws `Refused` carrying the server's words and the server's `spoke`; what those
 * mean to somebody reading them is `SAID`, and it is here. Written once, so that the two
 * callers of the same endpoint — the canvas and a dashboard tile — cannot disagree about
 * what a refusal is, which they did.
 *
 * Three cases, and the third is the one that is easy to lose: a server that failed without
 * saying what failed is not a validator with an empty list, and telling somebody to correct
 * what is named above when nothing is named above is worse than saying there is nothing to
 * act on but the status.
 */
export function refusal(error: unknown): Refusal {
  if (!(error instanceof Refused)) {
    return {
      kind: "refused",
      heading: "What the browser said",
      lines: [(error as Error).message],
      plain: "The request never reached the server.",
    };
  }
  if (error.spoke !== undefined) {
    return { kind: "refused", spoke: error.spoke, lines: error.errors, ...SAID[error.spoke] };
  }
  if (!error.said) {
    return {
      kind: "refused",
      heading: "What the server said",
      lines: error.errors,
      plain:
        "The server answered without saying what failed, so there is nothing here to act on but the status.",
    };
  }
  return {
    kind: "refused",
    heading: "What the validator said",
    lines: error.errors,
    plain: REJECTED,
  };
}

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
  // The step, so the region says the same thing the canvas does. It changes three times in
  // a question rather than once, which is three announcements — and that is the point:
  // somebody who cannot see the canvas is the person a blank wait is worst for.
  if (working !== null) return `${waiting(working).title}.`;
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
