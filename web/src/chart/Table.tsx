import { useMemo } from "react";
import { label, type Row } from "./option";

/**
 * The result set, in the builder's column order.
 *
 * Not a convenience. Three of the eight series colours sit under 3:1 against the light
 * surface, and the rule for a colour that does is visible labels or a table view. Interior
 * stacked segments cannot carry labels, so this is what makes those colours legal, and a
 * chart shipping them without it is a chart that fails the contrast rule.
 *
 * It shows the rows and shapes nothing. No sorting, no paging, no filtering: the query
 * already did all three, and a table that re-sorts what a chart drew is a second answer to
 * the question the chart answered.
 *
 * Column order is the result set contract: the keys of a row, in the order the builder
 * emitted them. It is read off the first row rather than off the spec, because the spec's
 * encoding names three of the columns and the query may produce more.
 */
export default function Table({ rows }: { rows: Row[] }) {
  // Memoised for its identity rather than for its cost: reading the keys is nothing, but a
  // fresh array every render is what made the memo below miss every time.
  const columns = useMemo(() => Object.keys(rows[0] ?? {}), [rows]);
  // Which columns are figures, decided once for the result set. Per cell it was a walk of
  // every row for every cell drawn: at the row cap the fixtures use that is two million row
  // visits to draw one table, on every switch to this tab.
  const figures = useMemo(() => numericColumns(rows, columns), [rows, columns]);

  if (columns.length === 0) {
    return (
      <div className="empty">
        <div>
          <p className="empty__title">No rows to show</p>
          <p className="empty__body">The query ran and returned nothing.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="table">
      <table className="table__grid">
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column} className={figures.has(column) ? "table__th--figure" : undefined}>
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, at) => (
            <tr key={at}>
              {columns.map((column) => (
                <td key={column} className={figures.has(column) ? "table__td--figure" : undefined}>
                  {label(row[column])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Which columns are columns of figures, which is what gets tabular numerals and the right
 * edge. Read off the values rather than off the channel type, because a table shows every
 * output column and only three of them are in an encoding. One pass over the rows for all
 * of the columns, deciding what the per-column walk decided: a column of numbers and nulls
 * with at least one number in it. */
const numericColumns = (rows: Row[], columns: string[]): Set<string> => {
  const numbers = new Set<string>();
  const others = new Set<string>();
  for (const row of rows) {
    for (const column of columns) {
      const value = row[column];
      if (typeof value === "number") numbers.add(column);
      else if (value !== null) others.add(column);
    }
  }
  return new Set([...numbers].filter((column) => !others.has(column)));
};

