import { useEffect, useState } from "react";
import Chart from "./Deferred";
import Table from "./Table";
import type { Drawn } from "./Chart";
import { copy, csv, download, fileName } from "./exporting";
import { overSeriesLimit, type Row, type Spec } from "./option";
import { NoDrill, candidates, drill, type Clicked } from "../spec/drill";
import { asDraft, type Draft, type Field } from "../spec/spec";

/**
 * The visual card: the chart, and the table of what it was drawn from.
 *
 * The Table tab is not a convenience. Slots 3, 4 and 5 of the series order sit under 3:1
 * against this surface, an interior stacked segment cannot carry a label, and the contrast
 * rule in docs/design.md then requires the numbers to be readable as text. This is what
 * makes those colours legal, which is why a view that draws marks and has no table beside
 * it is a view that fails the rule.
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
  // The instance, while there is one. State rather than a ref, because whether there is a
  // picture to save is a thing a control has to be disabled by, and a ref does not repaint.
  const [drawn, setDrawn] = useState<Drawn | null>(null);
  const [said, setSaid] = useState<string | null>(null);

  // What an export just did, for the four seconds a person needs to read it. Cleared on a
  // timer rather than left up, because it is about the press and not about the chart, and a
  // message that outlives what it describes is one nobody believes the next time.
  useEffect(() => {
    if (said === null) return;
    const forget = setTimeout(() => setSaid(null), 4000);
    return () => clearTimeout(forget);
  }, [said]);

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

  /**
   * The three ways out, in order of what they cost.
   *
   * The spec first, because it is the artefact this project is built around and there was
   * no control that gave anybody a copy of it. The rows second, because they are already
   * here in the builder's column order. The picture last, and only while there is one: the
   * renderer arrives behind a lazy boundary and the empty state and the figure draw no
   * canvas, so `drawn` is null exactly when there is nothing to save.
   */
  const copySpec = async () => {
    setSaid((await copy(JSON.stringify(spec, null, 2))) ? "Spec copied." : "The browser would not let this page write to the clipboard.");
  };

  const saveRows = () => {
    download(fileName(spec, "csv"), new Blob([csv(rows)], { type: "text/csv;charset=utf-8" }));
    setSaid(`Saved ${fileName(spec, "csv")}.`);
  };

  const saveImage = () => {
    if (drawn === null) return;
    const anchor = document.createElement("a");
    anchor.href = drawn.png();
    anchor.download = fileName(spec, "png");
    anchor.click();
    setSaid(`Saved ${fileName(spec, "png")}.`);
  };

  return (
    <div className="visual">
      <div className="visual__head">
        <span className="visual__out">
          <button className="visual__save" onClick={copySpec}>
            Copy the spec
          </button>
          <button className="visual__save" onClick={saveRows} disabled={rows.length === 0}>
            Rows as CSV
          </button>
          <button
            className="visual__save"
            onClick={saveImage}
            disabled={drawn === null || view !== "chart"}
            title={
              drawn === null
                ? "There is no chart on screen to save."
                : "A PNG of the chart, at twice the pixels."
            }
          >
            Chart as PNG
          </button>
        </span>
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
            onDrawn={setDrawn}
          />
        )}
      </div>

      {/* What the last press did. Polite, and one line: it is a receipt for a control the
          person just pressed, not a second account of what the canvas shows. */}
      <p className="visual__said" role="status">
        {said ?? ""}
      </p>

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
