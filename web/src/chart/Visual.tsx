import { useState } from "react";
import Chart from "./Deferred";
import Table from "./Table";
import { overSeriesLimit, type Row, type Spec } from "./option";
import { NoDrill, candidates, drill, type Clicked } from "../spec/drill";
import { asDraft, type Draft, type Field } from "../spec/spec";

/**
 * The visual card: the chart, and the table of what it was drawn from.
 *
 * The Table tab is not a convenience. Three of the series colours sit under 3:1 against
 * this surface, an interior stacked segment cannot carry a label, and the rule for a
 * colour that fails contrast is then visible labels or a table view. This is what makes
 * those colours legal.
 *
 * A click on a mark asks the same question about the thing that was clicked. What the
 * narrowed question is grouped by is asked rather than guessed, which is the one decision
 * a click cannot make on its own.
 */
export default function Visual({
  spec,
  rows,
  columns,
  onDrill,
}: {
  spec: Spec;
  rows: Row[];
  columns: Field[];
  onDrill: (draft: Draft) => void;
}) {
  const [view, setView] = useState<"chart" | "table">("chart");
  const [clicked, setClicked] = useState<Clicked | null>(null);
  const [refusal, setRefusal] = useState<string | null>(null);

  const tooMany = overSeriesLimit(spec, rows);
  const draft = asDraft(spec);
  const dimensions = clicked === null ? [] : candidates(draft, columns);

  const narrow = (by: Field) => {
    if (clicked === null) return;
    try {
      const narrowed = drill(spec, rows, clicked, by);
      setClicked(null);
      setRefusal(null);
      onDrill(narrowed);
    } catch (error) {
      if (!(error instanceof NoDrill)) throw error;
      setRefusal(error.message);
    }
  };

  return (
    <div className="visual">
      <div className="visual__head">
        <span className="visual__tabs">
          <button
            className={view === "chart" ? "visual__tab visual__tab--on" : "visual__tab"}
            onClick={() => setView("chart")}
            aria-pressed={view === "chart"}
          >
            Chart
          </button>
          <button
            className={view === "table" ? "visual__tab visual__tab--on" : "visual__tab"}
            onClick={() => setView("table")}
            aria-pressed={view === "table"}
          >
            Table
          </button>
        </span>
      </div>

      <div className="visual__body">
        {view === "table" ? (
          <Table rows={rows} />
        ) : tooMany !== null ? (
          <div className="refusal">
            <p className="refusal__head">What the renderer said</p>
            <ul className="refusal__lines">
              <li>{tooMany}</li>
            </ul>
            <p className="refusal__head">In plain terms</p>
            <p className="refusal__plain">
              Colours are assigned in a fixed order and never reused, so two series can never
              wear one colour. The rows are all there: the Table tab above shows them.
            </p>
          </div>
        ) : (
          <Chart
            spec={spec}
            rows={rows}
            onSelect={(mark) => {
              setRefusal(null);
              setClicked(mark);
            }}
          />
        )}
      </div>

      {clicked !== null ? (
        <div className="drill">
          <p className="drill__head">
            {refusal === null
              ? `Ask about ${label(clicked)}, per`
              : "That mark cannot be drilled into"}
          </p>
          {refusal !== null ? (
            <p className="drill__note">{refusal}</p>
          ) : dimensions.length === 0 ? (
            <p className="drill__note">
              The tables this chart reads hold no other column to group by. A column on
              another table would need a join, and a join a click made is a join nobody
              confirmed.
            </p>
          ) : (
            <ul className="drill__list">
              {dimensions.map((field) => (
                <li key={`${field.table}.${field.column}`}>
                  <button className="drill__pick" onClick={() => narrow(field)}>
                    <span className="drill__column">{field.column}</span>
                    <span className="drill__table">{field.table.split(".").slice(-1)[0]}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          <button className="drill__close" onClick={() => setClicked(null)}>
            Never mind
          </button>
        </div>
      ) : null}
    </div>
  );
}

const label = (clicked: Clicked): string =>
  clicked.series === undefined
    ? String(clicked.category ?? "(no value)")
    : `${clicked.category ?? "(no value)"} · ${clicked.series}`;
