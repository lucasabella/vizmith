import { label, type Row, type Value } from "./option";

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
  const columns = Object.keys(rows[0] ?? {});

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
              <th key={column} className={numeric(rows, column) ? "table__th--figure" : undefined}>
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, at) => (
            <tr key={at}>
              {columns.map((column) => (
                <td key={column} className={numeric(rows, column) ? "table__td--figure" : undefined}>
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

/** Whether a column is a column of figures, which is what gets tabular numerals and the
 * right edge. Read off the values rather than off the channel type, because a table shows
 * every output column and only three of them are in an encoding. */
const numeric = (rows: Row[], column: string): boolean =>
  rows.some((row) => typeof row[column] === "number") &&
  rows.every((row: Row) => isNumberOrNull(row[column]));

const isNumberOrNull = (value: Value): boolean => value === null || typeof value === "number";
