import { useEffect, useMemo, useState, type ReactNode } from "react";
import { getHealth, getShape, getTables, type Cost, type Suggestion } from "./api";
import { fromProfiles, fromShape, merged, type TableFields } from "./panels/field-data";
import Visual from "./chart/Visual";
import Fields from "./panels/Fields";
import Wells from "./panels/Wells";
import Data from "./views/Data";
import Dashboards from "./views/Dashboards";
import { drawable, type Field, type Spec } from "./spec/spec";
import Boundary from "./Boundary";
import { counted } from "./counted";
import { announced, waiting, type Outcome, type Working as WorkingState } from "./outcome";
import { useAsked } from "./asked";
import { ChartIcon, DashboardIcon, DataIcon } from "./icons";
import {
  NOTHING,
  editingIndex,
  putBack,
  tileTitle,
  type Arrangement,
  type Tile,
} from "./dashboard/dashboard";

/**
 * The views the rail switches between, in the order it draws them.
 *
 * A view used to be three separate edits — a button in the rail, a branch of a chain of
 * ternaries in the canvas, and a string in the `useState` union that nothing tied to either
 * — and the second was the one that got long. Here the union is the list, and `views` below
 * is a `Record` keyed by it, so a view added to `VIEWS` and nowhere else is a type error
 * that names the file and the missing key rather than a rail button that switches to a
 * blank canvas. It is the same exhaustiveness check `STEP` and `SAID` in `outcome.ts` get
 * from being keyed by the server's own unions.
 */
const VIEWS = ["chart", "dashboards", "data"] as const;

type ViewId = (typeof VIEWS)[number];

type View = {
  /** What the rail button is called, which is also the name a screen reader reads. */
  label: string;
  icon: ReactNode;
  /**
   * Whether the view is a page that scrolls, rather than the chart canvas.
   *
   * The chart canvas is a column that fits: a field at the top, a plot that takes what is
   * left, a strip at the bottom, and nothing that scrolls, because a chart that has to be
   * scrolled to is one nobody sees. Data and Dashboards are documents, so they get padding
   * and an overflow. One boolean rather than a class name in each entry, since these are
   * the only two surfaces `docs/design.md` allows.
   */
  page: boolean;
  render: () => ReactNode;
};

export default function App() {
  const [backend, setBackend] = useState<string | null>(null);
  const [source, setSource] = useState(false);
  const [model, setModel] = useState(false);
  const [question, setQuestion] = useState("");
  const [view, setView] = useState<ViewId>("chart");
  const [visualisationOpen, setVisualisationOpen] = useState(true);
  const [fieldsOpen, setFieldsOpen] = useState(true);
  const [json, setJson] = useState(false);
  const [tables, setTables] = useState<TableFields[] | null>(null);
  const [schemaFailure, setSchemaFailure] = useState<string | null>(null);
  const [dragging, setDragging] = useState<Field | null>(null);
  // The spec on screen, the request that produced it and the way back from it. One machine,
  // and it is in `asked.ts` so a test can drive it without rendering any of this.
  const asked = useAsked();
  // The dashboard being arranged. It lives here rather than in the view, because adding
  // the chart on screen to it means going back to the Chart view to build the next one,
  // and a view holding it would throw the arrangement away on the way out. Correcting a
  // tile is the same journey in reverse, which is why the tile being corrected is part of
  // it rather than a second piece of state somewhere else.
  const [arrangement, setArrangement] = useState<Arrangement>(NOTHING);

  useEffect(() => {
    getHealth()
      .then((said) => {
        setBackend(said.version);
        setSource(said.source);
        setModel(said.model);
      })
      .catch(() => setBackend(null));
  }, []);

  // The schema, in two requests, when there is a source to read it from.
  //
  // The shape first, because it is what the panel is actually drawn from: a table row is a
  // name and a count, a column row is a name and a type, and a drag reads the type and
  // nothing else. It costs no statement, so it comes back in the time a metadata read takes.
  // The profiles follow and fill in the figures, which is the wait that used to sit in front
  // of the first table name — modelled at 25 seconds and 456 billed statements on a schema
  // of 152 tables, all of it before anybody saw a word.
  //
  // Not the fan-out this replaced. One extra request for the whole schema, and the bulk
  // profile request behind it is exactly the one that was there before, so nothing here asks
  // the source when a table last changed a second time.
  //
  // Both write through the same state, and the profiles win: `merged` keeps a table the
  // shape knows and the profiles could not read, which is what a view looks like from here.
  useEffect(() => {
    if (!source) return;
    let live = true;
    let outline: TableFields[] = [];
    getShape()
      .then((body) => {
        if (!live) return;
        outline = fromShape(body.tables);
        // Only where the profiles have not already landed. They are the better answer, and
        // a slow shape request arriving second must not take the figures back off screen.
        setTables((filled) => filled ?? outline);
      })
      // A shape that failed is not reported: the profiles are the request that has to
      // work, and two failures on one panel would be one refusal argued twice.
      .catch(() => {});
    getTables()
      .then((body) => live && setTables(merged(outline, fromProfiles(body.tables))))
      .catch((error: Error) => live && setSchemaFailure(error.message));
    return () => {
      live = false;
    };
  }, [source]);

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
   * A tile opened for correction. Its spec goes into the editor and runs, and the view
   * changes to the one that can edit it, because a correction made anywhere else would be
   * a second editor to keep true. What is on screen before this is not saved anywhere, so
   * a tile is opened rather than swapped: the person asked for the tile.
   */
  const correct = (tile: Tile) => {
    setArrangement({ ...arrangement, editing: tile });
    asked.open(tile.spec);
    setView("chart");
  };

  /** The corrected spec, back in the tile it came from. It goes back only when it is
   * asked for: a tile that changed on every run would move under the person correcting
   * it, and half a spec is not a chart anybody wants on a dashboard. */
  const putItBack = () => {
    if (asked.draft === null || !drawable(asked.draft)) return;
    setArrangement(putBack(arrangement, asked.draft));
    setView("dashboards");
  };

  const stopCorrecting = () => setArrangement({ ...arrangement, editing: null });

  // Both halves have to be there: the model writes the spec and the source answers it.
  const askable = source && model;

  const columnsFor = [
    "var(--w-rail)",
    "1fr",
    visualisationOpen ? "var(--w-visualisation)" : "var(--w-shutter)",
    fieldsOpen ? "var(--w-fields)" : "var(--w-shutter)",
  ].join(" ");

  const views: Record<ViewId, View> = {
    chart: {
      label: "Chart",
      icon: <ChartIcon />,
      page: false,
      render: () => (
        <>
          <div className="ask">
            <div className="ask__field">
              <span className="ask__caret">&rsaquo;</span>
              <input
                className="ask__input"
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                onKeyDown={(event) => event.key === "Enter" && asked.askQuestion(question)}
                placeholder={
                  askable ? "Ask a question about your data" : "Finish setting Vizmith up to ask a question"
                }
                disabled={!askable || asked.running}
              />
              <span className="ask__key">Return</span>
            </div>
          </div>

          {/* `aria-busy` while a run is in flight, so what is under it is reported as
              changing rather than as the answer. */}
          <div className="plot" aria-busy={asked.running}>
            {/* The inner one. What the renderer draws is the part most likely to meet a
                value nobody planned for, and losing the chart is a much smaller loss
                than losing the wells, the editor and the dashboard being arranged —
                all of which are outside it and still there. The next outcome clears it,
                so one chart that could not be drawn does not refuse the ones after it. */}
            <Boundary
              what="chart"
              note="The spec is still in the editor and the panels beside it are untouched."
              resetOn={asked.outcome}
            >
              <Canvas
                outcome={asked.outcome}
                working={asked.working}
                source={source}
                model={model}
                columns={columns}
                onDrill={asked.drilled}
              />
            </Boundary>
          </div>

          {/* The page tabs that used to sit here were markup and did nothing. Several
              charts at once is the Dashboards view now, and a control that looks like it
              does that and does not is worse than not having one. */}
          <div className="pages">
            {asked.back === null ? null : (
              <button className="pages__back" onClick={asked.back}>
                &larr; the chart this came from
              </button>
            )}
            {/* The model reads the chart, so this is only offered where there is one to
                read and an endpoint to read it. */}
            {asked.outcome.kind === "chart" && askable ? (
              <SecondOpinion
                suggestion={asked.suggestion}
                asking={asked.suggesting}
                disabled={asked.running}
                onAsk={() => void asked.suggest()}
                onTake={asked.takeSuggestion}
                onDismiss={asked.dismissSuggestion}
              />
            ) : null}
            {arrangement.editing !== null ? (
              <Correcting
                arrangement={arrangement}
                drawable={asked.draft !== null && drawable(asked.draft)}
                onPutBack={putItBack}
                onStop={stopCorrecting}
              />
            ) : null}
            <span className="pages__meta">
              {asked.outcome.kind === "chart" ? counted(asked.outcome.rows.length, "row") : "no rows"}
              {asked.spent === null ? null : <Spent cost={asked.spent.cost} what={asked.spent.what} />}
            </span>
          </div>
        </>
      ),
    },
    dashboards: {
      label: "Dashboards",
      icon: <DashboardIcon />,
      page: true,
      // The spec on screen is what a dashboard adds, so the two views share the one draft
      // rather than the dashboard holding a copy that can drift from it.
      render: () => (
        <Dashboards
          current={asked.draft}
          columns={columns}
          arrangement={arrangement}
          onChange={setArrangement}
          onEdit={correct}
        />
      ),
    },
    data: {
      label: "Data",
      icon: <DataIcon />,
      page: true,
      render: () => <Data />,
    },
  };

  const current = views[view];

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
        <Badge outcome={asked.outcome} />
      </div>

      <div className="body" style={{ gridTemplateColumns: columnsFor }}>
        <nav className="rail">
          {VIEWS.map((id) => (
            <button
              key={id}
              className={view === id ? "rail__btn rail__btn--on" : "rail__btn"}
              title={views[id].label}
              aria-label={views[id].label}
              aria-current={view === id ? "page" : undefined}
              onClick={() => setView(id)}
            >
              {views[id].icon}
            </button>
          ))}
        </nav>

        <main className={current.page ? "canvas canvas--data" : "canvas"}>{current.render()}</main>

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
                    value={asked.text}
                    onChange={(event) => asked.retype(event.target.value)}
                    placeholder="Paste a spec, then run it."
                    // A placeholder is not a name: it is gone the moment there is text in
                    // the field, which is the whole time somebody is working in it.
                    aria-label="Chart specification, as JSON"
                    spellCheck={false}
                  />
                  <div className="spec__foot">
                    <button
                      className="btn"
                      onClick={asked.run}
                      disabled={!source || asked.running || asked.text === ""}
                    >
                      {asked.running ? "Running" : "Run spec"}
                    </button>
                  </div>
                </div>
              ) : (
                <Wells
                  draft={asked.draft}
                  dragging={dragging}
                  onChange={asked.edited}
                  onDrag={setDragging}
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
                holding={dragging}
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
        {announced(asked.outcome, asked.working)}
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
 * What the last model request cost, beside what it produced.
 *
 * The number is here because the first argument this project makes is that sending a
 * profile rather than rows keeps token cost bounded, and until now the figure that
 * demonstrates it was measured on every request and thrown away — so the claim was asked
 * for on trust by the audience most able to check it.
 *
 * Attempts are named rather than folded into the total, because they are the part that
 * surprises: a question the validator rejected twice cost three times one it accepted, and
 * a person watching one number go up has no way to know which happened. The breakdown
 * between prompt and completion is in the title, since it is the second question and not
 * the first.
 */
function Spent({ cost, what }: { cost: Cost; what: string }) {
  const tokens = cost.total.toLocaleString();
  return (
    <span
      className="pages__spent"
      title={`${cost.prompt.toLocaleString()} in the prompt, ${cost.completion.toLocaleString()} in the answer`}
    >
      {`· ${tokens} tokens on ${what}${cost.calls > 1 ? `, over ${cost.calls} attempts` : ""}`}
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
 * make the spec invalid, a model that never answered wrote none to judge, and a request the
 * server would not spend on never reached the thing that judges one. */
function Badge({ outcome }: { outcome: Outcome }) {
  const spoke = outcome.kind === "refused" ? outcome.spoke : undefined;
  if (outcome.kind === "nothing" || spoke === "model" || spoke === "rations") {
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
  working: WorkingState;
  source: boolean;
  model: boolean;
  columns: Field[];
  onDrill: (narrowed: Spec) => void;
}) {
  // What is in flight comes first. The chart that is still on screen answered the
  // previous question, which is not the one being waited for.
  if (working !== null) return <Working working={working} />;

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
 * different wording. See DESIGN.md.
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
 * it is not repeated here. Nothing counts anything down, because the server reports which
 * step is running and not how much of it is left: the dot says work is happening and the
 * words say which work, which is the half a spinner could never say.
 *
 * The sentences are `waiting` in `outcome.ts`, beside the ones a refusal is shown with, so
 * that what the canvas says and what the live region announces are one text.
 */
function Working({ working }: { working: NonNullable<WorkingState> }) {
  const { title, body } = waiting(working);
  return (
    <div className="working">
      <div>
        <i className="working__dot" />
        <p className="working__title">{title}</p>
        <p className="working__body">{body}</p>
      </div>
    </div>
  );
}
