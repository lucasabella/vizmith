import { useEffect, useMemo, useRef, useState } from "react";
import { critique, getTables, Refused, type Suggestion, type TableProfile } from "./api";
import Visual from "./chart/Visual";
import Fields from "./panels/Fields";
import Wells from "./panels/Wells";
import Data from "./views/Data";
import Dashboards from "./views/Dashboards";
import { draftIn, drawable, type Draft, type Field } from "./spec/spec";
import Boundary from "./Boundary";
import { counted } from "./counted";
import { announced, REJECTED, SAID, type Outcome, type Spoke, type Working } from "./outcome";
import { sequence } from "./runs";
import {
  NOTHING,
  editingIndex,
  putBack,
  tileTitle,
  type Arrangement,
  type Tile,
} from "./dashboard/dashboard";

/**
 * How many drills the way back holds.
 *
 * Every entry keeps the result set it replaced, so an unbounded history is one held copy
 * of every chart a person drilled through for as long as the tab is open, to support one
 * button. Ten is more steps than a drill path has and bounds what is kept; the oldest is
 * dropped, because the way back is walked from the newest end.
 */
const DRILLS_KEPT = 10;

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
  const [suggesting, setSuggesting] = useState(false);
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
   * not a spec parses to nothing and the wells go quiet, which is what a half typed spec
   * should do to them. What counts as a spec here is `draftIn`, which is in `spec.ts`
   * because that is where a test can reach it.
   */
  const draft = useMemo<Draft | null>(() => draftIn(text), [text]);

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
    // A finding is about the spec that was sent. Whatever is coming back is a different
    // one, so the second opinion goes rather than hanging over a chart it is not about.
    setSuggestion(null);
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
   * A second opinion on the chart that is on screen. It is asked for rather than offered,
   * because it is a request to a model and because a chart nothing refuses gets nothing
   * said about it, which is a control that would sit there doing nothing most of the time.
   *
   * The spec that drew the chart is what is sent, not what is in the editor: a finding is
   * about a chart that exists, and half a typed spec has not drawn one.
   */
  const askForASuggestion = async () => {
    if (outcome.kind !== "chart") return;
    setSuggesting(true);
    setSuggestion(null);
    try {
      setSuggestion(await critique(outcome.spec));
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

  /** A suggestion, taken. It replaces the chart and keeps the one it replaced, which is
   * what the drill already does and for the same reason: the spec it was about is one
   * control away. A suggestion nobody takes changes nothing at all. */
  const takeTheSuggestion = () => {
    const next = suggestion?.spec;
    if (!next) return;
    setBefore([...before, { text, outcome }].slice(-DRILLS_KEPT));
    setText(JSON.stringify(next, null, 2));
    send("/api/execute", { spec: next });
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
            aria-current={view === "chart" ? "page" : undefined}
            onClick={() => setView("chart")}
          >
            <ChartIcon />
          </button>
          <button
            className={view === "dashboards" ? "rail__btn rail__btn--on" : "rail__btn"}
            title="Dashboards"
            aria-label="Dashboards"
            aria-current={view === "dashboards" ? "page" : undefined}
            onClick={() => setView("dashboards")}
          >
            <DashboardIcon />
          </button>
          <button
            className={view === "data" ? "rail__btn rail__btn--on" : "rail__btn"}
            title="Data"
            aria-label="Data"
            aria-current={view === "data" ? "page" : undefined}
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

            {/* `aria-busy` while a run is in flight, so what is under it is reported as
                changing rather than as the answer. */}
            <div className="plot" aria-busy={running}>
              {/* The inner one. What the renderer draws is the part most likely to meet a
                  value nobody planned for, and losing the chart is a much smaller loss
                  than losing the wells, the editor and the dashboard being arranged —
                  all of which are outside it and still there. The next outcome clears it,
                  so one chart that could not be drawn does not refuse the ones after it. */}
              <Boundary
                what="chart"
                note="The spec is still in the editor and the panels beside it are untouched."
                resetOn={outcome}
              >
                <Canvas
                  outcome={outcome}
                  working={working}
                  source={source}
                  model={model}
                  columns={columns}
                  onDrill={drilled}
                />
              </Boundary>
            </div>

            {/* The page tabs that used to sit here were markup and did nothing. Several
                charts at once is the Dashboards view now, and a control that looks like it
                does that and does not is worse than not having one. */}
            <div className="pages">
              {before.length > 0 ? (
                <button className="pages__back" onClick={back}>
                  &larr; the chart this came from
                </button>
              ) : null}
              {/* The model reads the chart, so this is only offered where there is one to
                  read and an endpoint to read it. */}
              {outcome.kind === "chart" && askable ? (
                <SecondOpinion
                  suggestion={suggestion}
                  asking={suggesting}
                  disabled={running}
                  onAsk={askForASuggestion}
                  onTake={takeTheSuggestion}
                  onDismiss={() => setSuggestion(null)}
                />
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
                {outcome.kind === "chart" ? counted(outcome.rows.length, "row") : "no rows"}
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

      {/* The one region that carries the outcome. Everything this interface says appears by
          being swapped into the tree, which a screen reader does not report, and the canvas
          is where the answer to a question lands. Polite and one sentence: see `announced`
          for why it is not the whole refusal.

          Outside the view, not inside the Chart view's own markup, which is where it was
          first put. A live region is only reported when its contents change while it is in
          the document, so one that leaves with the view is one that announces nothing to
          somebody who asked a question and went to look at a dashboard while it ran — and
          on the readers that do announce an inserted region, it would say the same sentence
          again on every return. Out here it is in the document for the life of the tab. */}
      <p className="visually-hidden" role="status" aria-live="polite">
        {announced(outcome, working)}
      </p>
    </div>
  );
}

/**
 * A second opinion on the chart on screen, and the two ways out of it.
 *
 * What it may say is what a rule refuses, which is why "nothing to suggest" is a real
 * answer and the common one rather than a failure: the alternative is an assistant that
 * always finds something, and what it finds is somebody's taste. The words are the server's
 * — the rule wrote them, and rewording them here would be a second account of one thing.
 *
 * Never mind sits next to Use it for the same reason it sits next to Put it back: a change
 * a person did not ask for is not one this makes on their behalf. Taking it keeps the chart
 * it replaced, so the way back is the one the drill already put there.
 */
function SecondOpinion({
  suggestion,
  asking,
  disabled,
  onAsk,
  onTake,
  onDismiss,
}: {
  suggestion: Suggestion | null;
  asking: boolean;
  disabled: boolean;
  onAsk: () => void;
  onTake: () => void;
  onDismiss: () => void;
}) {
  if (suggestion === null) {
    return (
      <span className="pages__second">
        <button className="btn btn--quiet" onClick={onAsk} disabled={asking || disabled}>
          {asking ? "Reading the chart" : "Suggest an improvement"}
        </button>
      </span>
    );
  }

  const said = [...suggestion.findings, ...suggestion.errors].join(" ");
  const nothing = said === "";
  return (
    <span className="pages__second">
      <span className="pages__note pages__note--said" title={said}>
        {nothing ? "Nothing to suggest: no rule refuses this chart." : said}
      </span>
      {suggestion.spec === null ? null : (
        <button className="btn btn--small" onClick={onTake} disabled={disabled}>
          Use it
        </button>
      )}
      <button className="btn btn--quiet" onClick={onDismiss}>
        Never mind
      </button>
    </span>
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
  working: Working;
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
