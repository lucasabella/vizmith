import { useEffect, useMemo, useRef, useState } from "react";
import { critique, getTables, Refused, type Suggestion, type TableProfile } from "./api";
import Visual from "./chart/Visual";
import type { Row, Spec } from "./chart/option";
import Fields from "./panels/Fields";
import Wells from "./panels/Wells";
import Data from "./views/Data";
import Dashboards from "./views/Dashboards";
import { drawable, type Draft, type Field } from "./spec/spec";
import { sequence } from "./runs";
import {
  NOTHING,
  editingIndex,
  putBack,
  tileTitle,
  type Arrangement,
  type Tile,
} from "./dashboard/dashboard";

/** Which part refused, as the server named it. It is the only thing that can: a question
 * passes through the source, the model and the source again, and from here they are one
 * request. */
type Spoke = "source" | "model" | "spec";

/**
 * What the canvas is showing. A refusal carries the machine's own words and a sentence
 * that says what they mean, because one without the other is either unreadable or
 * unverifiable.
 */
type Outcome =
  | { kind: "nothing" }
  | { kind: "chart"; spec: Spec; rows: Row[] }
  | { kind: "refused"; heading: string; lines: string[]; plain: string; spoke?: Spoke };

/**
 * How many drills the way back holds.
 *
 * Every entry keeps the result set it replaced, so an unbounded history is one held copy
 * of every chart a person drilled through for as long as the tab is open, to support one
 * button. Ten is more steps than a drill path has and bounds what is kept; the oldest is
 * dropped, because the way back is walked from the newest end.
 */
const DRILLS_KEPT = 10;

const REJECTED =
  "The spec did not pass validation, so nothing ran against the source. Correct what is named above and run it again.";

const SAID: Record<Spoke, { heading: string; plain: string }> = {
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

export default function App() {
  const [backend, setBackend] = useState<string | null>(null);
  const [source, setSource] = useState(false);
  const [model, setModel] = useState(false);
  const [question, setQuestion] = useState("");
  const [view, setView] = useState<"chart" | "dashboards" | "data">("chart");
  const [visualisationOpen, setVisualisationOpen] = useState(true);
  const [fieldsOpen, setFieldsOpen] = useState(true);
  const [json, setJson] = useState(false);
  const [text, setText] = useState("");
  const [tables, setTables] = useState<TableProfile[] | null>(null);
  const [schemaFailure, setSchemaFailure] = useState<string | null>(null);
  const [dragging, setDragging] = useState<Field | null>(null);
  // What is in flight. Not an Outcome: running is the absence of one so far.
  const [working, setWorking] = useState<"question" | "spec" | null>(null);
  const [outcome, setOutcome] = useState<Outcome>({ kind: "nothing" });
  // Which run is the one being waited for. A ref rather than state: nothing drawn depends
  // on it, so a render for each change would be a render for nothing.
  const runs = useRef(sequence());
  // Where a drill came from. A drill that cannot be undone is a trap, so the spec that was
  // replaced stays reachable, as the text and the chart it drew. Taking a suggestion goes
  // on the same stack, so the spec a critique was about is the one control back.
  const [before, setBefore] = useState<{ text: string; outcome: Outcome }[]>([]);
  // What a critique said about the chart on screen, once it has been asked for. Null is
  // "not asked", an empty list is "asked, and the rules found nothing", and the two are
  // different sentences to a person who pressed the button.
  const [advice, setAdvice] = useState<Suggestion[] | null>(null);
  const [advising, setAdvising] = useState(false);
  const [adviceFailure, setAdviceFailure] = useState<string | null>(null);
  // The dashboard being arranged. It lives here rather than in the view, because adding
  // the chart on screen to it means going back to the Chart view to build the next one,
  // and a view holding it would throw the arrangement away on the way out. Correcting a
  // tile is the same journey in reverse, which is why the tile being corrected is part of
  // it rather than a second piece of state somewhere else.
  const [arrangement, setArrangement] = useState<Arrangement>(NOTHING);
  const running = working !== null;

  useEffect(() => {
    fetch("/api/health")
      .then((response) => response.json())
      .then((body) => {
        setBackend(body.version);
        setSource(body.source);
        setModel(body.model);
      })
      .catch(() => setBackend(null));
  }, []);

  // The schema, once, when there is a source to read it from. Every table's profile
  // rather than the list alone: the panel shows a column's profile, the wells need its
  // type to infer anything, and the server profiled all of them on the first request
  // anyway. The same figures the model is given, from the same endpoint — in one request,
  // rather than a listing and one request per table after it, which asked the source when
  // each table last changed a second time.
  useEffect(() => {
    if (!source) return;
    let live = true;
    getTables()
      .then((body) => live && setTables(body.tables))
      .catch((error: Error) => live && setSchemaFailure(error.message));
    return () => {
      live = false;
    };
  }, [source]);

  /**
   * The spec on screen, parsed. The wells and `{ } JSON` are two views of one spec rather
   * than two specs, so this is the one place it lives and both write to it. Text that is
   * not JSON parses to nothing and the wells go quiet, which is what a half typed spec
   * should do to them.
   */
  const draft = useMemo<Draft | null>(() => {
    try {
      const parsed = JSON.parse(text);
      return parsed !== null && typeof parsed === "object" ? (parsed as Draft) : null;
    } catch {
      return null;
    }
  }, [text]);

  /** Every column of every table, which is what a well drags and what a drill offers. */
  const columns = useMemo<Field[]>(
    () =>
      (tables ?? []).flatMap((table) =>
        table.columns.map((column) => ({
          table: table.table,
          column: column.name,
          type: column.type,
        })),
      ),
    [tables],
  );

  /**
   * The API decides. Nothing here validates, because a second opinion in the browser is
   * one that can disagree with the one that counts. `question` is what was asked, which
   * says what the canvas waits with and means the spec that comes back replaces whatever
   * is in the editor. A spec that was typed passes none, and keeps its own text.
   */
  const send = async (endpoint: string, payload: object, question: string | null = null) => {
    // Which run this is. Only the one still being waited for writes: see `runs.ts` for the
    // answer that would otherwise be drawn under the wrong spec.
    const latest = runs.current.start();
    setWorking(question === null ? "spec" : "question");
    // Advice about the chart that is being replaced is advice about a chart nobody is
    // looking at any more, and a suggestion for it would apply to the wrong spec.
    setAdvice(null);
    setAdviceFailure(null);
    try {
      const response = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = await response.json();
      if (!latest()) return;
      if (response.ok) {
        setOutcome({ kind: "chart", spec: body.spec, rows: body.rows });
        if (question !== null) setText(JSON.stringify(body.spec, null, 2));
      } else if (body.spoke) {
        setOutcome({ kind: "refused", spoke: body.spoke, lines: body.errors, ...SAID[body.spoke as Spoke] });
      } else if (body.errors) {
        setOutcome({ kind: "refused", heading: "What the validator said", lines: body.errors, plain: REJECTED });
      } else {
        setOutcome({
          kind: "refused",
          heading: "What the server said",
          lines: [`${response.status} ${response.statusText}`],
          plain: "The server answered without saying what failed, so there is nothing here to act on but the status.",
        });
      }
    } catch (error) {
      if (!latest()) return;
      setOutcome({
        kind: "refused",
        heading: "What the browser said",
        lines: [(error as Error).message],
        plain: "The request never reached the server.",
      });
    } finally {
      // Only the latest run clears it, so a superseded answer arriving first does not take
      // the canvas off "Running the spec" while the run being waited for is still in flight.
      if (latest()) setWorking(null);
    }
  };

  const run = () => {
    try {
      send("/api/execute", { spec: JSON.parse(text) });
    } catch (error) {
      setOutcome({
        kind: "refused",
        heading: "What the parser said",
        lines: [(error as Error).message],
        plain: "The spec is not valid JSON, so it was never sent.",
      });
    }
  };

  /** The model writes the spec, so the answer replaces whatever is in the editor. */
  const askQuestion = () => {
    if (question.trim() !== "") send("/api/ask", { question }, question);
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
    if (drawable(next)) send("/api/execute", { spec: next });
  };

  /** A drill replaces the chart, and keeps the one it replaced. */
  const drilled = (next: Draft) => {
    setBefore([...before, { text, outcome }].slice(-DRILLS_KEPT));
    setText(JSON.stringify(next, null, 2));
    send("/api/execute", { spec: next });
  };

  /**
   * Ask what is wrong with the chart on screen. The spec that produced the rows is what is
   * sent, not the text in the editor: a spec somebody has half edited since is not the one
   * they are looking at.
   *
   * This is a button rather than something that happens on its own, because it runs the
   * spec again and pays for a request per fault. What comes back changes nothing until a
   * suggestion is taken.
   */
  const check = async () => {
    if (outcome.kind !== "chart" || advising) return;
    setAdvising(true);
    setAdviceFailure(null);
    try {
      const body = await critique(outcome.spec);
      setAdvice(body.suggestions);
    } catch (error) {
      setAdvice(null);
      setAdviceFailure(
        error instanceof Refused ? error.errors.join(" ") : (error as Error).message,
      );
    } finally {
      setAdvising(false);
    }
  };

  /**
   * A suggestion the person took. It runs like any other spec and goes on the way back, so
   * the chart it was about is one control away — a suggestion that could not be undone
   * would be the interface changing somebody's spec for them.
   */
  const took = (suggested: Spec) => {
    setBefore([...before, { text, outcome }].slice(-DRILLS_KEPT));
    setText(JSON.stringify(suggested, null, 2));
    send("/api/execute", { spec: suggested });
  };

  /**
   * A tile opened for correction. Its spec goes into the editor and runs, and the view
   * changes to the one that can edit it, because a correction made anywhere else would be
   * a second editor to keep true. What is on screen before this is not saved anywhere, so
   * a tile is opened rather than swapped: the person asked for the tile.
   */
  const correct = (tile: Tile) => {
    setArrangement({ ...arrangement, editing: tile });
    setText(JSON.stringify(tile.spec, null, 2));
    send("/api/execute", { spec: tile.spec });
    setView("chart");
  };

  /** The corrected spec, back in the tile it came from. It goes back only when it is
   * asked for: a tile that changed on every run would move under the person correcting
   * it, and half a spec is not a chart anybody wants on a dashboard. */
  const putItBack = () => {
    if (draft === null || !drawable(draft)) return;
    setArrangement(putBack(arrangement, draft));
    setView("dashboards");
  };

  const stopCorrecting = () => setArrangement({ ...arrangement, editing: null });

  const back = () => {
    const previous = before[before.length - 1];
    if (previous === undefined) return;
    setBefore(before.slice(0, -1));
    setText(previous.text);
    setOutcome(previous.outcome);
  };

  // Both halves have to be there: the model writes the spec and the source answers it.
  const askable = source && model;

  const columnsFor = [
    "var(--w-rail)",
    "1fr",
    visualisationOpen ? "var(--w-visualisation)" : "var(--w-shutter)",
    fieldsOpen ? "var(--w-fields)" : "var(--w-shutter)",
  ].join(" ");

  return (
    <div className="app">
      <header className="chrome">
        <span className="chrome__brand">vizmith</span>
        <span className="pill">
          <i className={source ? "pill__dot pill__dot--live" : "pill__dot"} />
          {source ? "source configured" : "no source"}
        </span>
        {/* The model endpoint is the other half of asking a question, and health already
            reports it, so the chrome says whether it is there rather than leaving the
            disabled question field to explain itself. */}
        <span className="pill">
          <i className={model ? "pill__dot pill__dot--live" : "pill__dot"} />
          {model ? "model configured" : "no model endpoint"}
        </span>
        <span className="chrome__spacer" />
        <span className="pill">
          <i className={backend ? "pill__dot pill__dot--live" : "pill__dot"} />
          {backend ? `backend ${backend}` : "backend unreachable"}
        </span>
      </header>

      <div className="strip">
        <span className="strip__text">
          The model writes <b>the query and the chart spec</b>. Everything else on this screen is code.
        </span>
        <Badge outcome={outcome} />
      </div>

      <div className="body" style={{ gridTemplateColumns: columnsFor }}>
        <nav className="rail">
          <button
            className={view === "chart" ? "rail__btn rail__btn--on" : "rail__btn"}
            title="Chart"
            aria-label="Chart"
            aria-pressed={view === "chart"}
            onClick={() => setView("chart")}
          >
            <ChartIcon />
          </button>
          <button
            className={view === "dashboards" ? "rail__btn rail__btn--on" : "rail__btn"}
            title="Dashboards"
            aria-label="Dashboards"
            aria-pressed={view === "dashboards"}
            onClick={() => setView("dashboards")}
          >
            <DashboardIcon />
          </button>
          <button
            className={view === "data" ? "rail__btn rail__btn--on" : "rail__btn"}
            title="Data"
            aria-label="Data"
            aria-pressed={view === "data"}
            onClick={() => setView("data")}
          >
            <DataIcon />
          </button>
        </nav>

        {view === "data" ? (
          <main className="canvas canvas--data">
            <Data />
          </main>
        ) : view === "dashboards" ? (
          // The spec on screen is what a dashboard adds, so the two views share the one
          // draft rather than the dashboard holding a copy that can drift from it.
          <main className="canvas canvas--data">
            <Dashboards
              current={draft}
              arrangement={arrangement}
              onChange={setArrangement}
              onEdit={correct}
            />
          </main>
        ) : (
          <main className="canvas">
            <div className="ask">
              <div className="ask__field">
                <span className="ask__caret">&rsaquo;</span>
                <input
                  className="ask__input"
                  value={question}
                  onChange={(event) => setQuestion(event.target.value)}
                  onKeyDown={(event) => event.key === "Enter" && askQuestion()}
                  placeholder={
                    askable ? "Ask a question about your data" : "Finish setting Vizmith up to ask a question"
                  }
                  disabled={!askable || running}
                />
                <span className="ask__key">Return</span>
              </div>
            </div>

            <div className="plot">
              <Canvas
                outcome={outcome}
                working={working}
                source={source}
                model={model}
                columns={columns}
                onDrill={drilled}
              />
            </div>

            {/* The page tabs that used to sit here were markup and did nothing. Several
                charts at once is the Dashboards view now, and a control that looks like it
                does that and does not is worse than not having one. */}
            {advice !== null || adviceFailure !== null ? (
              <Advice
                suggestions={advice}
                failure={adviceFailure}
                disabled={running}
                onTake={took}
                onDismiss={() => {
                  setAdvice(null);
                  setAdviceFailure(null);
                }}
              />
            ) : null}

            <div className="pages">
              {before.length > 0 ? (
                <button className="pages__back" onClick={back}>
                  &larr; the chart this came from
                </button>
              ) : null}
              {outcome.kind === "chart" && model ? (
                <button
                  className="pages__back"
                  onClick={check}
                  disabled={advising || running}
                  title="Run the rules over this chart and ask the model to correct what they find"
                >
                  {advising ? "Checking this chart" : "What is wrong with this chart?"}
                </button>
              ) : null}
              {arrangement.editing !== null ? (
                <Correcting
                  arrangement={arrangement}
                  drawable={draft !== null && drawable(draft)}
                  onPutBack={putItBack}
                  onStop={stopCorrecting}
                />
              ) : null}
              <span className="pages__meta">
                {outcome.kind === "chart" ? `${outcome.rows.length} rows` : "no rows"}
              </span>
            </div>
          </main>
        )}

        {visualisationOpen ? (
          <section className="panel">
            <div className="panel__head">
              <span className="panel__title">Visualisation</span>
              <span className="panel__actions">
                <button
                  className={json ? "panel__json panel__json--on" : "panel__json"}
                  onClick={() => setJson(!json)}
                  aria-pressed={json}
                >
                  {"{ } JSON"}
                </button>
                <button
                  className="panel__collapse"
                  onClick={() => setVisualisationOpen(false)}
                  aria-label="Collapse Visualisation"
                >
                  &#9654;
                </button>
              </span>
            </div>
            <div className="panel__body">
              {json ? (
                <div className="spec">
                  <textarea
                    className="spec__text"
                    value={text}
                    onChange={(event) => setText(event.target.value)}
                    placeholder="Paste a spec, then run it."
                    // A placeholder is not a name: it is gone the moment there is text in
                    // the field, which is the whole time somebody is working in it.
                    aria-label="Chart specification, as JSON"
                    spellCheck={false}
                  />
                  <div className="spec__foot">
                    <button className="btn" onClick={run} disabled={!source || running || text === ""}>
                      {running ? "Running" : "Run spec"}
                    </button>
                  </div>
                </div>
              ) : (
                <Wells
                  draft={draft}
                  dragging={dragging}
                  onChange={edited}
                  onRelationships={() => setView("data")}
                />
              )}
            </div>
          </section>
        ) : (
          <aside className="shutter">
            <button onClick={() => setVisualisationOpen(true)} aria-label="Expand Visualisation">
              &#9664;
            </button>
            <span className="shutter__label">Visualisation</span>
          </aside>
        )}

        {fieldsOpen ? (
          <section className="panel">
            <div className="panel__head">
              <span className="panel__title">Fields</span>
              <span className="panel__actions">
                <button
                  className="panel__collapse"
                  onClick={() => setFieldsOpen(false)}
                  aria-label="Collapse Fields"
                >
                  &#9654;
                </button>
              </span>
            </div>
            <div className="panel__body">
              <Fields
                tables={source ? tables : []}
                failure={schemaFailure}
                onDrag={setDragging}
              />
            </div>
            <div className="panel__foot">
              <span className="panel__foot-h">Profile only</span>
              <p className="panel__foot-b">
                Types, counts and value ranges go to the model. Rows go from the source straight to the
                chart.
              </p>
            </div>
          </section>
        ) : (
          <aside className="shutter">
            <button onClick={() => setFieldsOpen(true)} aria-label="Expand Fields">
              &#9664;
            </button>
            <span className="shutter__label">Fields</span>
          </aside>
        )}
      </div>
    </div>
  );
}

/**
 * What a critique found, and the one control that takes each of it.
 *
 * The sentence is the server's, because the rule that found the fault is the only thing
 * that can say what it is. Nothing here judges a suggestion or ranks them: what is shown
 * is what the rules named, in the order they named it.
 *
 * An empty list is a sentence rather than an empty box. A person who pressed a button and
 * got nothing back cannot tell "the rules found nothing" from "the request went nowhere",
 * and those are different things to do next about.
 */
function Advice({
  suggestions,
  failure,
  disabled,
  onTake,
  onDismiss,
}: {
  suggestions: Suggestion[] | null;
  failure: string | null;
  disabled: boolean;
  onTake: (spec: Spec) => void;
  onDismiss: () => void;
}) {
  return (
    <section className="advice">
      <div className="advice__head">
        <span className="advice__title">
          {failure !== null
            ? "The check did not finish"
            : suggestions === null || suggestions.length === 0
              ? "Nothing to correct"
              : `${suggestions.length} thing${suggestions.length === 1 ? "" : "s"} to correct`}
        </span>
        <button className="btn btn--quiet" onClick={onDismiss}>
          Close
        </button>
      </div>
      {failure !== null ? (
        <p className="advice__empty">{failure}</p>
      ) : suggestions === null || suggestions.length === 0 ? (
        <p className="advice__empty">
          The rules read the spec, the profiles and the shape of the result, and none of them
          had anything to say about this chart. They do not have an opinion about which mark
          is nicest, so a chart nothing is wrong with gets nothing back.
        </p>
      ) : (
        <ul className="advice__list">
          {suggestions.map((suggestion) => (
            <li className="advice__item" key={suggestion.rule}>
              <span className="advice__rule">{suggestion.rule}</span>
              <span className="advice__says">{suggestion.says}</span>
              <button
                className="btn btn--small"
                onClick={() => onTake(suggestion.spec)}
                disabled={disabled}
              >
                Use this one
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/**
 * The tile being corrected, and the two ways out of it.
 *
 * A correction that cannot be abandoned is the same trap a drill without a way back is,
 * so Never mind is next to Put it back rather than somewhere else. A tile that was removed
 * while it was being corrected has nowhere to go back to, and that is said rather than
 * left for the button to do nothing about.
 */
function Correcting({
  arrangement,
  drawable,
  onPutBack,
  onStop,
}: {
  arrangement: Arrangement;
  drawable: boolean;
  onPutBack: () => void;
  onStop: () => void;
}) {
  const at = editingIndex(arrangement);
  const tile = arrangement.editing;
  return (
    <span className="pages__correcting">
      {at === -1 || tile === null ? (
        <span className="pages__note">
          The tile you were correcting is no longer on the dashboard.
        </span>
      ) : (
        <>
          <span className="pages__note">
            Correcting <b>{tileTitle(tile, at)}</b>
            {arrangement.name === "" ? null : ` on ${arrangement.name}`}
          </span>
          <button className="btn btn--small" onClick={onPutBack} disabled={!drawable}>
            Put it back
          </button>
        </>
      )}
      <button className="btn btn--quiet" onClick={onStop}>
        Never mind
      </button>
    </span>
  );
}

/** The badge reports the spec and nothing else. A source that refused a statement did not
 * make the spec invalid, and a model that never answered wrote none to judge. */
function Badge({ outcome }: { outcome: Outcome }) {
  const spoke = outcome.kind === "refused" ? outcome.spoke : undefined;
  if (outcome.kind === "nothing" || spoke === "model") {
    return <span className="strip__badge">no spec yet</span>;
  }
  const valid = outcome.kind === "chart" || spoke === "source";
  return (
    <span className={`strip__badge strip__badge--${valid ? "good" : "bad"}`}>
      {valid ? "spec valid" : "spec rejected"}
    </span>
  );
}

function Canvas({
  outcome,
  working,
  source,
  model,
  columns,
  onDrill,
}: {
  outcome: Outcome;
  working: "question" | "spec" | null;
  source: boolean;
  model: boolean;
  columns: Field[];
  onDrill: (draft: Draft) => void;
}) {
  // What is in flight comes first. The chart that is still on screen answered the
  // previous question, which is not the one being waited for.
  if (working !== null) return <Working asking={working === "question"} />;

  if (outcome.kind === "chart") {
    return <Visual spec={outcome.spec} rows={outcome.rows} columns={columns} onDrill={onDrill} />;
  }

  if (outcome.kind === "refused") {
    return (
      <div className="refusal">
        <p className="refusal__head">{outcome.heading}</p>
        <ul className="refusal__lines">
          {outcome.lines.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
        <p className="refusal__head">In plain terms</p>
        <p className="refusal__plain">{outcome.plain}</p>
      </div>
    );
  }

  return (
    <div className="empty">
      <div>
        <p className="empty__title">{source ? "No spec yet" : "No source connected"}</p>
        <p className="empty__body">
          {source && model
            ? "Ask a question above, drag a column from Fields into a well, or open { } JSON and paste a spec. The chart appears here."
            : "Point Vizmith at a Databricks workspace and at a model endpoint. It reads the schema and profiles every column, then you can ask a question."}
        </p>
        {source && model ? null : <Setup source={source} model={model} />}
      </div>
    </div>
  );
}

/**
 * What is missing and what to do about it, in one place.
 *
 * Configuration is server side and stays that way: a request that cannot name a database
 * is a request that cannot be pointed at one, which is a sentence worth keeping. What that
 * costs is this screen, and what makes it bearable is that the thing it names is a command
 * rather than a file somebody has to find. Said once, here, rather than in two panels with
 * different wording. See ROADMAP.md.
 */
function Setup({ source, model }: { source: boolean; model: boolean }) {
  return (
    <div className="setup">
      <p className="setup__head">
        Run <code>vizmith configure</code>, then restart the server
      </p>
      {source ? null : (
        <p className="setup__line">
          <span className="setup__what">The source</span>
          <code>
            VIZMITH_DATABRICKS_PROFILE, CATALOG, SCHEMA, WAREHOUSE
          </code>
          <span className="setup__why">Reads the schema, profiles the columns and runs the query.</span>
        </p>
      )}
      {model ? null : (
        <p className="setup__line">
          <span className="setup__what">The model endpoint</span>
          <code>VIZMITH_MODEL_BASE_URL, NAME, KEY</code>
          <span className="setup__why">
            Writes a spec from a question. Without it a spec still runs; only asking is off.
          </span>
        </p>
      )}
      {/* The command asks for these and writes them where only you can read them. An
          environment variable and a .env in the working directory both win over that
          file, which is what keeps a checkout working the way it always did. */}
      <p className="setup__note">
        <code>vizmith configure --show</code> says where each one is coming from. A real
        environment variable, or a <code>.env</code> where you started the server, wins over
        what the command wrote.
      </p>
    </div>
  );
}

/**
 * The wait, and what is being waited for. The question itself stays in the field above, so
 * it is not repeated here. The server reports no progress, so nothing counts anything
 * down: the dot says work is happening and the words say which work. The profiling
 * sentence is the one worth reading, because it is both the reason a first question is slow
 * and the reason the model never sees a row.
 */
function Working({ asking }: { asking: boolean }) {
  return (
    <div className="working">
      <div>
        <i className="working__dot" />
        <p className="working__title">{asking ? "Answering the question" : "Running the spec"}</p>
        <p className="working__body">
          {asking
            ? "A first question reads the schema and profiles every column before the model is asked anything, and a warehouse that was idle has to start before any of it runs. A profile is kept until the table it describes changes, so the next question skips it, and so does the next restart."
            : "The source is running the query. The rows come back to the chart and go nowhere near the model."}
        </p>
      </div>
    </div>
  );
}

function ChartIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
      <rect x="2" y="9" width="3.4" height="7" fill="currentColor" />
      <rect x="7.3" y="5" width="3.4" height="11" fill="currentColor" />
      <rect x="12.6" y="2" width="3.4" height="14" fill="currentColor" />
    </svg>
  );
}

function DashboardIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
      <rect x="2" y="2" width="6" height="7" fill="none" stroke="currentColor" strokeWidth="1.4" />
      <rect x="10" y="2" width="6" height="4" fill="none" stroke="currentColor" strokeWidth="1.4" />
      <rect x="2" y="11" width="6" height="5" fill="none" stroke="currentColor" strokeWidth="1.4" />
      <rect x="10" y="8" width="6" height="8" fill="none" stroke="currentColor" strokeWidth="1.4" />
    </svg>
  );
}

function DataIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
      <rect x="2" y="3" width="14" height="12" fill="none" stroke="currentColor" strokeWidth="1.4" />
      <line x1="2" y1="7" x2="16" y2="7" stroke="currentColor" strokeWidth="1.4" />
      <line x1="7" y1="7" x2="7" y2="15" stroke="currentColor" strokeWidth="1.4" />
    </svg>
  );
}
