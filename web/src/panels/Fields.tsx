import { useState } from "react";
import type { ColumnProfile } from "../api";
import type { Field } from "../spec/spec";
import type { ColumnFields, TableFields } from "./fields";

/**
 * The tables, their columns, and what the profile says about a column.
 *
 * This is the screen that proves what the model was allowed to see. Everything in it comes
 * from the two GET endpoints, which read the same profile the prompt path reads, so the
 * panel's claim is checkable rather than decorative.
 *
 * No row from any table appears here. A sample list is the vocabulary of a low cardinality
 * column, which is metadata, and the threshold that decides which columns have one is the
 * profiler's rather than this panel's.
 *
 * A column whose type the catalog reports as unsupported is not in the profile and is
 * therefore not in this tree. That is deliberate: a profile describes what can be charted.
 *
 * It is filled twice. The shape arrives first and costs no statement, so a table name and a
 * column type are on screen in the time a metadata read takes rather than in the time
 * profiling a schema takes; the profiles land behind it and fill in the figures. What that
 * asks of this file is that every row draws from the half that has arrived and says which
 * half that is — a count that is not read yet shows nothing rather than a zero, and a
 * column that is not profiled yet says so rather than showing an empty sample list, because
 * a blank where a vocabulary goes reads as a column with no values.
 */
export default function Fields({
  tables,
  failure,
  onDrag,
}: {
  tables: TableFields[] | null;
  failure: string | null;
  onDrag: (field: Field | null) => void;
}) {
  const [open, setOpen] = useState<string | null>(null);

  if (failure !== null) {
    return (
      <div className="fields">
        <p className="fields__note">{failure}</p>
      </div>
    );
  }

  if (tables === null) {
    return (
      <div className="fields">
        <p className="fields__note">Reading the schema.</p>
      </div>
    );
  }

  if (tables.length === 0) {
    return (
      <div className="fields">
        <p className="fields__note">
          Tables and their column profiles appear here once a source is connected.
        </p>
      </div>
    );
  }

  return (
    <div className="fields">
      {tables.map((table) => (
        <TableNode
          key={table.table}
          table={table}
          open={open === table.table}
          onToggle={() => setOpen(open === table.table ? null : table.table)}
          onDrag={onDrag}
        />
      ))}
    </div>
  );
}

function TableNode({
  table,
  open,
  onToggle,
  onDrag,
}: {
  table: TableFields;
  open: boolean;
  onToggle: () => void;
  onDrag: (field: Field | null) => void;
}) {
  return (
    <div className="tree__table">
      <button className="tree__row tree__row--table" onClick={onToggle} aria-expanded={open}>
        <span className="tree__twist" aria-hidden="true">
          {open ? "▾" : "▸"}
        </span>
        <span className="tree__name">{last(table.table)}</span>
        {/* The count comes from a pass over the table, so it arrives with the profile and
            not with the shape. Nothing is better than a zero: a zero is a claim about the
            data, and this is a claim about what has been read. */}
        <span className="tree__count">{table.row_count === null ? "" : count(table.row_count)}</span>
      </button>
      {open
        ? table.columns.map((column) => (
            <ColumnNode key={column.name} table={table.table} column={column} onDrag={onDrag} />
          ))
        : null}
    </div>
  );
}

function ColumnNode({
  table,
  column,
  onDrag,
}: {
  table: string;
  column: ColumnFields;
  onDrag: (field: Field | null) => void;
}) {
  const [open, setOpen] = useState(false);

  return (
    <div>
      <div
        className="tree__row tree__row--column"
        draggable
        onDragStart={() => onDrag({ table, column: column.name, type: column.type })}
        onDragEnd={() => onDrag(null)}
        onClick={() => setOpen(!open)}
        role="button"
        tabIndex={0}
        // A control that says it is a button answers Space as well as Enter, which is what
        // the role promises and what the table row above gets for free by being one. This
        // is a div because it is also the drag source, and `draggable` on a button is
        // awkward — that is a reason for the shape and not for answering one key. Space
        // scrolls the page unless something says otherwise, and a real button says so.
        onKeyDown={(event) => {
          if (event.key !== "Enter" && event.key !== " ") return;
          event.preventDefault();
          setOpen(!open);
        }}
        aria-expanded={open}
      >
        <span className="tree__grip" aria-hidden="true">
          ⠿
        </span>
        <span className="tree__name">{column.name}</span>
        <span className="tree__type">{column.type}</span>
      </div>
      {open ? column.profile === null ? <Unread /> : <Profile column={column.profile} /> : null}
    </div>
  );
}

/**
 * A column the shape knows and the profiles have not reached yet.
 *
 * Not an empty profile, which is what drawing `Profile` against absent figures would be.
 * Every one of them — no nulls, no distinct values, no vocabulary — is the strongest claim
 * this panel can make about a column, and this is the panel whose whole job is to prove
 * what the model was allowed to see. "Not read yet" and "read, and there is nothing" are
 * different sentences and only one of them is true here.
 */
export function Unread() {
  return (
    <p className="profile__none profile__none--waiting">
      The figures for this column have not been read yet.
    </p>
  );
}

/**
 * A column's profile, inline rather than in a tooltip. It is the element that carries the
 * project's whole reason to exist, and a tooltip is where a thing goes to be seen by
 * accident.
 *
 * A figure the source estimated says so. `distinct_count_exact` exists for exactly this:
 * a profile is cheap by requirement, so a distinct count is approximate wherever the
 * source has an approximate function, and an estimate shown as a fact is what that field
 * is there to prevent.
 */
export function Profile({ column }: { column: ColumnProfile }) {
  const ranged = column.minimum !== null || column.maximum !== null;

  return (
    <dl className="profile">
      <Figure name="type" value={column.type} />
      <Figure
        name="distinct"
        value={`${column.distinct_count.toLocaleString("en-GB")}${
          column.distinct_count_exact ? "" : " approx."
        }`}
      />
      <Figure name="nulls" value={nullRate(column.null_rate)} />
      {ranged ? <Figure name="range" value={`${column.minimum ?? "—"} … ${column.maximum ?? "—"}`} /> : null}
      <dt className="profile__key">values</dt>
      <dd className="profile__value">
        {column.samples.length > 0 ? (
          <span className="profile__samples">
            {column.samples.map((sample) => (
              <span key={sample} className="profile__sample">
                {sample}
              </span>
            ))}
          </span>
        ) : (
          // An empty list reads as "this column has no values". The truth is that it has
          // too many to show, and those are different answers.
          <span className="profile__none">
            too many distinct values to list, so none were read
          </span>
        )}
      </dd>
    </dl>
  );
}

function Figure({ name, value }: { name: string; value: string }) {
  return (
    <>
      <dt className="profile__key">{name}</dt>
      <dd className="profile__value profile__value--figure">{value}</dd>
    </>
  );
}

/**
 * The null rate, rounded here and nowhere else. The profile keeps it unrounded on purpose,
 * because one null in a million rows must not become a column reporting no nulls, so a
 * rate that is not zero never prints as zero: it prints as the smallest thing that is
 * still true.
 */
export function nullRate(rate: number): string {
  if (rate === 0) return "none";
  if (rate >= 0.995) return "all";
  const percent = rate * 100;
  return percent < 0.1 ? "<0.1%" : `${percent.toFixed(percent < 10 ? 1 : 0)}%`;
}

const count = (rows: number): string => `${rows.toLocaleString("en-GB")} rows`;

const last = (name: string): string => name.split(".").slice(-1)[0];
