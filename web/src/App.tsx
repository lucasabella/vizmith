import { useEffect, useState } from "react";

const WELLS = ["Axis", "Legend", "Values", "Top N", "Filters"];

export default function App() {
  const [backend, setBackend] = useState<string | null>(null);
  const [visualisationOpen, setVisualisationOpen] = useState(true);
  const [fieldsOpen, setFieldsOpen] = useState(true);

  useEffect(() => {
    fetch("/api/health")
      .then((response) => response.json())
      .then((body) => setBackend(body.version))
      .catch(() => setBackend(null));
  }, []);

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
          <i className="pill__dot" />
          no source
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
        <span className="strip__badge">no spec yet</span>
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
                placeholder="Connect a source before asking a question"
                disabled
              />
              <span className="ask__key">Return</span>
            </div>
          </div>

          <div className="plot">
            <div className="empty">
              <div>
                <p className="empty__title">No source connected</p>
                <p className="empty__body">
                  Point Vizmith at a Databricks workspace. It reads the schema and profiles every column,
                  then you can ask a question.
                </p>
              </div>
            </div>
          </div>

          <div className="pages">
            <span className="pages__tab pages__tab--on">Page 1</span>
            <span className="pages__tab pages__tab--add">+ page</span>
            <span className="pages__meta">no rows</span>
          </div>
        </main>

        {visualisationOpen ? (
          <section className="panel">
            <div className="panel__head">
              <span className="panel__title">Visualisation</span>
              <span className="panel__actions">
                <span className="panel__json">{"{ } JSON"}</span>
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
              <div className="wells">
                {WELLS.map((well) => (
                  <div key={well}>
                    <span className="well__name">{well}</span>
                    <div className="well__drop">Drop a field here</div>
                  </div>
                ))}
              </div>
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
