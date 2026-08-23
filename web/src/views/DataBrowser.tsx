import { useState } from "react";
import { Profile, Unread } from "../panels/Fields";
import type { TableFields } from "../panels/field-data";

export default function DataBrowser({
  tables,
  failure,
  connected,
}: {
  tables: TableFields[] | null;
  failure: string | null;
  connected: boolean;
}) {
  const [chosenTable, setChosenTable] = useState<string | null>(null);
  const [chosenColumn, setChosenColumn] = useState<string | null>(null);

  if (failure !== null) {
    return (
      <div className="source-data">
        <p className="source-data__note">{failure}</p>
      </div>
    );
  }

  if (!connected) {
    return (
      <div className="source-data">
        <p className="source-data__eyebrow">Source inspection</p>
        <h1 className="source-data__title">Data</h1>
        <p className="source-data__note">
          Tables and column profiles appear here once a source is connected.
        </p>
      </div>
    );
  }

  if (tables === null) {
    return (
      <div className="source-data">
        <p className="source-data__note">Reading the schema.</p>
      </div>
    );
  }

  if (tables.length === 0) {
    return (
      <div className="source-data">
        <p className="source-data__note">This source has no profiled tables.</p>
      </div>
    );
  }

  const table = tables.find((candidate) => candidate.table === chosenTable) ?? tables[0];
  const column =
    table.columns.find((candidate) => candidate.name === chosenColumn) ?? table.columns[0] ?? null;

  const chooseTable = (name: string) => {
    setChosenTable(name);
    setChosenColumn(null);
  };

  return (
    <div className="source-data">
      <header className="source-data__head">
        <div>
          <p className="source-data__eyebrow">Source inspection</p>
          <h1 className="source-data__title">Data</h1>
          <p className="source-data__lead">
            Inspect the tables and profiles Vizmith uses without exposing individual rows.
          </p>
        </div>
        <span className="source-data__scope">Profiles only</span>
      </header>

      <div className="source-data__layout">
        <aside className="source-data__tables" aria-label="Tables">
          <h2>Tables</h2>
          <div className="source-data__table-list">
            {tables.map((candidate) => (
              <button
                key={candidate.table}
                className={candidate.table === table.table ? "is-on" : ""}
                onClick={() => chooseTable(candidate.table)}
                aria-current={candidate.table === table.table ? "true" : undefined}
              >
                <strong>{last(candidate.table)}</strong>
                <small>{rows(candidate.row_count)}</small>
              </button>
            ))}
          </div>
        </aside>

        <section className="source-data__workspace">
          <header className="source-data__table-head">
            <div>
              <span>Selected table</span>
              <h2>{last(table.table)}</h2>
            </div>
            <b>{rows(table.row_count)}</b>
          </header>

          <div className="source-data__details">
            <div className="source-data__columns" aria-label={`Columns in ${last(table.table)}`}>
              {table.columns.map((candidate) => (
                <button
                  key={candidate.name}
                  className={candidate.name === column?.name ? "is-on" : ""}
                  onClick={() => setChosenColumn(candidate.name)}
                  aria-current={candidate.name === column?.name ? "true" : undefined}
                >
                  <strong>{candidate.name}</strong>
                  <span>{candidate.type}</span>
                </button>
              ))}
            </div>

            <aside className="source-data__profile">
              <span className="source-data__profile-label">Column profile</span>
              {column === null ? (
                <p className="source-data__note">This table has no profiled columns.</p>
              ) : (
                <>
                  <h3>{`${last(table.table)}.${column.name}`}</h3>
                  {column.profile === null ? <Unread /> : <Profile column={column.profile} />}
                </>
              )}
            </aside>
          </div>

          <footer className="source-data__provenance">
            <span><b>Model</b> column names, types, counts and ranges</span>
            <span><b>Local</b> individual rows and query results</span>
          </footer>
        </section>
      </div>
    </div>
  );
}

const last = (name: string): string => name.split(".").slice(-1)[0];

const rows = (count: number | null): string =>
  count === null ? "profile pending" : `${count.toLocaleString("en-GB")} rows`;
