import { useEffect, useState } from "react";
import Chart from "./chart/Chart";
import type { Row, Spec } from "./chart/option";

const WELLS = ["Axis", "Legend", "Values", "Top N", "Filters"];

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
  const [visualisationOpen, setVisualisationOpen] = useState(true);
  const [fieldsOpen, setFieldsOpen] = useState(true);
  const [json, setJson] = useState(false);
  const [text, setText] = useState("");
  // What is in flight. Not an Outcome: running is the absence of one so far.
  const [working, setWorking] = useState<"question" | "spec" | null>(null);
  const [outcome, setOutcome] = useState<Outcome>({ kind: "nothing" });
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

  /**
   * The API decides. Nothing here validates, because a second opinion in the browser is
   * one that can disagree with the one that counts. `question` is what was asked, which
   * says what the canvas waits with and means the spec that comes back replaces whatever
   * is in the editor. A spec that was typed passes none, and keeps its own text.
   */
  const send = async (endpoint: string, payload: object, question: string | null = null) => {
    setWorking(question === null ? "spec" : "question");
    try {
      const response = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = await response.json();
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
      setOutcome({
        kind: "refused",
        heading: "What the browser said",
        lines: [(error as Error).message],
        plain: "The request never reached the server.",
      });
    } finally {
      setWorking(null);
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

  // Both halves have to be there: the model writes the spec and the source answers it.
  const askable = source && model;
  const waiting = !source
    ? "Connect a source before asking a question"
    : "Set VIZMITH_MODEL_BASE_URL, NAME and KEY to ask a question";

  const columns = [
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

      <div className="body" style={{ gridTemplateColumns: columns }}>
        <nav className="rail">
          <button className="rail__btn rail__btn--on" title="Chart" aria-label="Chart">
            <ChartIcon />
          </button>
          <button className="rail__btn" title="Data" aria-label="Data">
            <DataIcon />
          </button>
        </nav>

        <main className="canvas">
          <div className="ask">
            <div className="ask__field">
              <span className="ask__caret">&rsaquo;</span>
              <input
                className="ask__input"
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                onKeyDown={(event) => event.key === "Enter" && askQuestion()}
                placeholder={askable ? "Ask a question about your data" : waiting}
                disabled={!askable || running}
              />
              <span className="ask__key">Return</span>
            </div>
          </div>

          <div className="plot">
            <Canvas outcome={outcome} working={working} source={source} askable={askable} />
          </div>

          <div className="pages">
            <span className="pages__tab pages__tab--on">Page 1</span>
            <span className="pages__tab pages__tab--add">+ page</span>
            <span className="pages__meta">
              {outcome.kind === "chart" ? `${outcome.rows.length} rows` : "no rows"}
            </span>
          </div>
        </main>

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
                    spellCheck={false}
                  />
                  <div className="spec__foot">
                    <button className="btn" onClick={run} disabled={!source || running || text === ""}>
                      {running ? "Running" : "Run spec"}
                    </button>
                    {source ? null : (
                      <p className="spec__note">
                        Running needs a source. Set <code>VIZMITH_DATABRICKS_PROFILE</code>,{" "}
                        <code>CATALOG</code>, <code>SCHEMA</code> and <code>WAREHOUSE</code>, then restart
                        the server.
                      </p>
                    )}
                  </div>
                </div>
              ) : (
                <div className="wells">
                  {WELLS.map((well) => (
                    <div key={well}>
                      <span className="well__name">{well}</span>
                      <div className="well__drop">Drop a field here</div>
                    </div>
                  ))}
                </div>
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
              <div className="fields">
                <p className="fields__note">
                  Tables and their column profiles appear here once a source is connected.
                </p>
              </div>
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
  askable,
}: {
  outcome: Outcome;
  working: "question" | "spec" | null;
  source: boolean;
  askable: boolean;
}) {
  // What is in flight comes first. The chart that is still on screen answered the
  // previous question, which is not the one being waited for.
  if (working !== null) return <Working asking={working === "question"} />;

  if (outcome.kind === "chart") return <Chart spec={outcome.spec} rows={outcome.rows} />;

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
          {askable
            ? "Ask a question above, or open { } JSON in the Visualisation panel and paste a spec. The chart appears here."
            : source
              ? "Open { } JSON in the Visualisation panel, paste a spec and run it. The chart appears here."
              : "Point Vizmith at a Databricks workspace. It reads the schema and profiles every column, then you can ask a question."}
        </p>
      </div>
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
            ? "A first question reads the schema and profiles every column before the model is asked anything, and a warehouse that was idle has to start before any of it runs. The profiles are kept for as long as the server runs, so the next question skips them."
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

function DataIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
      <rect x="2" y="3" width="14" height="12" fill="none" stroke="currentColor" strokeWidth="1.4" />
      <line x1="2" y1="7" x2="16" y2="7" stroke="currentColor" strokeWidth="1.4" />
      <line x1="7" y1="7" x2="7" y2="15" stroke="currentColor" strokeWidth="1.4" />
    </svg>
  );
}
