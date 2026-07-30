import { useEffect, useState } from "react";
import Chart from "./chart/Chart";
import type { Row, Spec } from "./chart/option";

const WELLS = ["Axis", "Legend", "Values", "Top N", "Filters"];

/**
 * What the canvas is showing. A refusal carries the machine's own words and a sentence
 * that says what they mean, because one without the other is either unreadable or
 * unverifiable.
 */
type Outcome =
  | { kind: "nothing" }
  | { kind: "chart"; spec: Spec; rows: Row[] }
  | { kind: "refused"; heading: string; lines: string[]; plain: string };

const REJECTED =
  "The spec did not pass validation, so nothing ran against the source. Correct what is named above and run it again.";

export default function App() {
  const [backend, setBackend] = useState<string | null>(null);
  const [source, setSource] = useState(false);
  const [visualisationOpen, setVisualisationOpen] = useState(true);
  const [fieldsOpen, setFieldsOpen] = useState(true);
  const [json, setJson] = useState(false);
  const [text, setText] = useState("");
  const [running, setRunning] = useState(false);
  const [outcome, setOutcome] = useState<Outcome>({ kind: "nothing" });

  useEffect(() => {
    fetch("/api/health")
      .then((response) => response.json())
      .then((body) => {
        setBackend(body.version);
        setSource(body.source);
      })
      .catch(() => setBackend(null));
  }, []);

  /**
   * The spec goes to the API and the API decides. Nothing here validates, because a
   * second opinion in the browser is one that can disagree with the one that counts.
   */
  const run = async () => {
    let spec: Spec;
    try {
      spec = JSON.parse(text);
    } catch (error) {
      setOutcome({
        kind: "refused",
        heading: "What the parser said",
        lines: [(error as Error).message],
        plain: "The spec is not valid JSON, so it was never sent.",
      });
      return;
    }

    setRunning(true);
    try {
      const response = await fetch("/api/execute", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ spec }),
      });
      const body = await response.json();
      if (response.ok) {
        setOutcome({ kind: "chart", spec, rows: body.rows });
      } else if (body.errors) {
        setOutcome({ kind: "refused", heading: "What the validator said", lines: body.errors, plain: REJECTED });
      } else {
        setOutcome({
          kind: "refused",
          heading: "What the server said",
          lines: [`${response.status} ${response.statusText}`],
          plain: "The spec passed validation and the query did not run. The source is what to check.",
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
      setRunning(false);
    }
  };

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
                placeholder={
                  source
                    ? "A model endpoint is not configured yet"
                    : "Connect a source before asking a question"
                }
                disabled
              />
              <span className="ask__key">Return</span>
            </div>
          </div>

          <div className="plot">
            <Canvas outcome={outcome} source={source} />
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

function Badge({ outcome }: { outcome: Outcome }) {
  if (outcome.kind === "nothing") return <span className="strip__badge">no spec yet</span>;
  const rejected = outcome.kind === "refused";
  return (
    <span className={`strip__badge strip__badge--${rejected ? "bad" : "good"}`}>
      {rejected ? "spec rejected" : "spec valid"}
    </span>
  );
}

function Canvas({ outcome, source }: { outcome: Outcome; source: boolean }) {
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
          {source
            ? "Open { } JSON in the Visualisation panel, paste a spec and run it. The chart appears here."
            : "Point Vizmith at a Databricks workspace. It reads the schema and profiles every column, then you can ask a question."}
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
