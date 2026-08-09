import { useMemo, useRef, useState } from "react";
import { ask, critique, execute, Refused, type Answered, type Cost, type Step, type Suggestion } from "./api";
import { refusal, type Outcome, type Working } from "./outcome";
import { sequence } from "./runs";
import { draftIn, drawable, type Draft, type Spec } from "./spec/spec";

/**
 * The machine that turns a request into what the canvas shows.
 *
 * Text goes in — typed, dragged into a well, drilled, suggested or asked as a question — a
 * request goes out, and one of three things comes back: a chart, a refusal, or an answer
 * that is no longer the one being waited for and is dropped. That is one machine with one
 * rule, and it was spread through `App.tsx` beside the panel shutters and the view rail,
 * where the only way to run it was to render the whole application. This is that machine on
 * its own, so a test can hold it still: `renderHook` gives it a lifetime without a DOM, and
 * the properties that used to need a rendered tree — a superseded answer is dropped, a
 * refusal carries its cost, a drill keeps the chart it replaced — are asserted against the
 * thing that holds them rather than through three panels of markup.
 *
 * What it deliberately does not own is anything a person can see that is not the answer:
 * which view the rail is on, whether a panel is open, what is in the question field, the
 * dashboard being arranged. Those are arrangement, this is the answer, and the seam between
 * them is the reason the file divides where it does. The one place they touch is a tile
 * opened for correction, which is `open` here and the view change in `App.tsx`.
 */

/**
 * How many drills the way back holds.
 *
 * Every entry keeps the result set it replaced, so an unbounded history is one held copy
 * of every chart a person drilled through for as long as the tab is open, to support one
 * button. Ten is more steps than a drill path has and bounds what is kept; the oldest is
 * dropped, because the way back is walked from the newest end.
 */
const DRILLS_KEPT = 10;

/** What the last model request cost, and which request it was. */
export type Spent = { cost: Cost; what: string };

export type Asked = {
  /** The spec on screen, as text. The editor writes it and everything else replaces it. */
  text: string;
  /** The same spec, parsed, or nothing where the text is not one. */
  draft: Draft | null;
  outcome: Outcome;
  working: Working;
  /** Whether a request is in flight. Several controls are disabled by it, and it is the
   * absence of an outcome rather than a kind of one. */
  running: boolean;
  spent: Spent | null;
  suggestion: Suggestion | null;
  suggesting: boolean;
  retype: (text: string) => void;
  run: () => void;
  askQuestion: (question: string) => void;
  edited: (next: Draft) => void;
  drilled: (next: Spec) => void;
  open: (spec: Spec) => void;
  suggest: () => Promise<void>;
  takeSuggestion: () => void;
  dismissSuggestion: () => void;
  /** The way back to the chart a drill or a suggestion replaced, or nothing where there is
   * no such chart. A function or null rather than a function and a count: the caller draws
   * the control when there is somewhere to go, and there is no second way to ask. */
  back: (() => void) | null;
};

export function useAsked(): Asked {
  const [text, setText] = useState("");
  // What is in flight. Not an Outcome: running is the absence of one so far.
  const [working, setWorking] = useState<Working>(null);
  const [outcome, setOutcome] = useState<Outcome>({ kind: "nothing" });
  // Which run is the one being waited for. A ref rather than state: nothing drawn depends
  // on it, so a render for each change would be a render for nothing.
  const runs = useRef(sequence());
  // Where a drill came from. A drill that cannot be undone is a trap, so the spec that was
  // replaced stays reachable, as the text and the chart it drew. Taking a suggestion goes
  // on the same stack, because it replaces a chart the same way a drill does.
  const [before, setBefore] = useState<{ text: string; outcome: Outcome }[]>([]);
  // The second opinion, once it has been asked for. Null is nobody asked; it is cleared on
  // every run, because a finding is about the spec that was sent and the next chart is a
  // different spec.
  const [suggestion, setSuggestion] = useState<Suggestion | null>(null);
  // What the last model request cost, and which request it was. The claim this design is
  // built on is that sending metadata rather than data keeps token cost bounded, and the
  // number that shows it was measured on every request and never left the server. Held for
  // the last request rather than accumulated: a running total for the tab would answer a
  // question nobody asked and would hide the one that matters, which is what this question
  // cost.
  const [spent, setSpent] = useState<Spent | null>(null);
  const [suggesting, setSuggesting] = useState(false);

  /**
   * The spec on screen, parsed. The wells and `{ } JSON` are two views of one spec rather
   * than two specs, so this is the one place it lives and both write to it. Text that is
   * not a spec parses to nothing and the wells go quiet, which is what a half typed spec
   * should do to them. What counts as a spec here is `draftIn`, which is in `spec.ts`
   * because that is where a test can reach it.
   */
  const draft = useMemo<Draft | null>(() => draftIn(text), [text]);

  /**
   * The API decides. Nothing here validates, because a second opinion in the browser is
   * one that can disagree with the one that counts. `question` is what was asked, which
   * says what the canvas waits with and means the spec that comes back replaces whatever
   * is in the editor. A spec that was typed passes none, and keeps its own text.
   */
  const send = async (
    asking: (watching: (step: Step) => void) => Promise<Answered>,
    question: string | null = null,
  ) => {
    // Which run this is. Only the one still being waited for writes: see `runs.ts` for the
    // answer that would otherwise be drawn under the wrong spec.
    const latest = runs.current.start();
    setWorking({ asking: question !== null, step: null });
    // A finding is about the spec that was sent. Whatever is coming back is a different
    // one, so the second opinion goes rather than hanging over a chart it is not about.
    setSuggestion(null);
    try {
      // The step the server last reported, and only while this run is still the one being
      // waited for: a superseded question still has a stream open, and a step off it would
      // say the interface is doing something it stopped doing.
      const answered = await asking((step) => latest() && setWorking({ asking: true, step }));
      if (!latest()) return;
      // A question reports what it cost, whether or not it produced a spec — three attempts
      // and nothing to show is the expensive case. Running a spec by hand reaches no model,
      // so it carries no cost and clears the last one rather than leaving it under a chart
      // it is not about.
      setSpent(answered.cost ? { cost: answered.cost, what: "this question" } : null);
      setOutcome({ kind: "chart", spec: answered.spec, rows: answered.rows });
      if (question !== null) setText(JSON.stringify(answered.spec, null, 2));
    } catch (error) {
      if (!latest()) return;
      setOutcome(refusal(error));
      // A refusal can carry a cost too, and the expensive refusal is the one that took
      // three attempts and produced nothing.
      const cost = error instanceof Refused ? error.cost : undefined;
      setSpent(cost ? { cost, what: "this question" } : null);
    } finally {
      // Only the latest run clears it, so a superseded answer arriving first does not take
      // the canvas off "Running the spec" while the run being waited for is still in flight.
      if (latest()) setWorking(null);
    }
  };

  /**
   * The text in the editor, run.
   *
   * Parsed here rather than inside the callback `send` awaits. It used to be the latter,
   * and the difference is not stylistic: a throw inside that callback lands in `send`'s own
   * `catch`, which shows every failure as `refusal(error)` — "What the browser said", "The
   * request never reached the server." So the parser branch below could not be reached at
   * all, and a missing brace was reported as a transport failure. It went unnoticed for as
   * long as it did because this lived in `App.tsx`, which is #158's whole argument.
   */
  const run = () => {
    let parsed: Spec;
    try {
      parsed = JSON.parse(text) as Spec;
    } catch (error) {
      setOutcome({
        kind: "refused",
        heading: "What the parser said",
        lines: [(error as Error).message],
        plain: "The spec is not valid JSON, so it was never sent.",
      });
      return;
    }
    void send(() => execute(parsed));
  };

  /** The model writes the spec, so the answer replaces whatever is in the editor.
   *
   * The question is passed in rather than held here, because the field it is typed into is
   * arrangement: it keeps its text while a question runs, it survives a trip to another
   * view, and none of that is this machine's business.
   *
   * The steps the server reports on the way there are `send`'s to write down, because it
   * is the one holding the ticket that says whether this run is still the one on screen. */
  const askQuestion = (question: string) => {
    if (question.trim() !== "") void send((watching) => ask(question, watching), question);
  };

  /**
   * A well was edited. The spec it produced goes to `/api/execute`, which validates before
   * it reaches the source, so a well that produced something illegal shows the validator's
   * words and runs no query.
   *
   * A spec with no measure is not sent. It is not a spec that failed, it is one that is
   * not finished, and answering an unfinished spec with a required property error would
   * put a refusal on screen for every drop but the last.
   */
  const edited = (next: Draft) => {
    setText(JSON.stringify(next, null, 2));
    if (drawable(next)) void send(() => execute(next));
  };

  /** A spec that replaces the chart, keeping the one it replaced. A drill and a taken
   * suggestion are the same move: both put a different chart where one already was, and
   * both are a trap without a way back. */
  const insteadOf = (next: Spec) => {
    setBefore([...before, { text, outcome }].slice(-DRILLS_KEPT));
    setText(JSON.stringify(next, null, 2));
    void send(() => execute(next));
  };

  /**
   * A second opinion on the chart that is on screen. It is asked for rather than offered,
   * because it is a request to a model and because a chart nothing refuses gets nothing
   * said about it, which is a control that would sit there doing nothing most of the time.
   *
   * The spec that drew the chart is what is sent, not what is in the editor: a finding is
   * about a chart that exists, and half a typed spec has not drawn one.
   */
  const suggest = async () => {
    if (outcome.kind !== "chart") return;
    setSuggesting(true);
    setSuggestion(null);
    try {
      const said = await critique(outcome.spec);
      setSuggestion(said);
      if (said.cost && said.cost.calls > 0) setSpent({ cost: said.cost, what: "this suggestion" });
    } catch (error) {
      // In the same shape a suggestion arrives in, so the strip has one thing to draw. The
      // server's own words where there are any: a message written here would be a second
      // account of something that already has one.
      setSuggestion({
        findings: [],
        spec: null,
        errors: error instanceof Refused ? error.errors : [(error as Error).message],
      });
    } finally {
      setSuggesting(false);
    }
  };

  const takeSuggestion = () => {
    const next = suggestion?.spec;
    if (next) insteadOf(next);
  };

  const previous = before[before.length - 1];
  const back =
    previous === undefined
      ? null
      : () => {
          setBefore(before.slice(0, -1));
          setText(previous.text);
          setOutcome(previous.outcome);
        };

  return {
    text,
    draft,
    outcome,
    working,
    running: working !== null,
    spent,
    suggestion,
    suggesting,
    retype: setText,
    run,
    askQuestion,
    edited,
    drilled: insteadOf,
    /**
     * A spec opened from somewhere else — today a dashboard tile being corrected. It
     * replaces what is on screen without going on the way back, because what it replaces
     * was not saved anywhere and the tile it came from is the way back.
     */
    open: (spec: Spec) => {
      setText(JSON.stringify(spec, null, 2));
      void send(() => execute(spec));
    },
    suggest,
    takeSuggestion,
    dismissSuggestion: () => setSuggestion(null),
    back,
  };
}
