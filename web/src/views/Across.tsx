import { useState } from "react";
import { channelType, COMPARISONS, type Field, type Filter } from "../spec/spec";
import { type Across as Filters, describe, dimensions, reach, typeOf } from "../dashboard/across";
import { FILTER_LIMIT, type Tile } from "../dashboard/dashboard";
import { counted } from "../counted";

/** The operators the bar offers: the grammar's comparisons, and the two that take no value.
 * `in` and `not_in` are not here — a list is a control of its own and a person who wants
 * one of two values adds two filters, which the grammar reads as an `and` and not the `or`
 * they meant. That is the one thing this bar cannot say, and it is said in DESIGN.md. */
const CHOSEN = [...COMPARISONS, "is_null", "is_not_null"] as const;
type Chosen = (typeof CHOSEN)[number];

/**
 * The one control on a dashboard that reaches more than one tile.
 *
 * What it builds is a filter, and the whole of the argument for why a filter rather than a
 * shared axis or a linked selection is in `across.ts`. What is here is the two halves a
 * person sees: the filters that are on, as chips that say what they do and how far they
 * reach, and the form that makes one.
 *
 * The menu of columns is the dimensions the tiles are already grouped by, not the schema.
 * A dashboard is about what its charts are about, and a menu of every column of every table
 * would be a hundred and fifty entries of which four reach a tile.
 *
 * A date column is offered the grammar's relative values — today, the start of this month,
 * three months ago — because those are what a saved dashboard needs. A stored `2026-08-08`
 * means that day forever, and a dashboard whose date filter has to be retyped every morning
 * is one nobody keeps. The spec on disk stays relative and the builder resolves it when it
 * compiles the query, which is a thing the grammar could already do and nothing offered.
 */
export default function Across({
  across,
  tiles,
  columns,
  onChange,
}: {
  across: Filters;
  tiles: Tile[];
  columns: Field[];
  onChange: (next: Filters) => void;
}) {
  const offered = dimensions(tiles);
  const [column, setColumn] = useState("");
  const [op, setOp] = useState<Chosen>("=");
  const [kind, setKind] = useState<"typed" | "today" | "start_of" | "ago">("typed");
  const [unit, setUnit] = useState<"year" | "quarter" | "month" | "week" | "day">("month");
  const [text, setText] = useState("");

  // The column the form is about, which is the first one offered until somebody picks
  // another. A select whose value is "" would be a fourth state to write a message for.
  const about = column === "" ? (offered[0] ?? "") : column;
  const type = typeOf(about, columns);
  const temporal = channelType(type) === "temporal";
  const quantitative = channelType(type) === "quantitative";
  const needsValue = op !== "is_null" && op !== "is_not_null";
  // A relative value is only on offer for a date. On any other column the tokens mean
  // nothing the source could compare, so the select is not drawn and the state is ignored.
  const relative = temporal ? kind : "typed";

  const value = made(relative, unit, text, quantitative);
  // The cap is the grammar's own, held to it by `mirrors.test.ts`. Stopping here rather
  // than at the save is what the tile count already does: a dashboard refused whole for a
  // seventeenth filter is a refusal about something nobody had to add.
  const addable = about !== "" && across.length < FILTER_LIMIT && (!needsValue || value !== undefined);

  const add = () => {
    if (!addable) return;
    const filter = needsValue
      ? ({ column: about, op, value } as Filter)
      : ({ column: about, op } as Filter);
    onChange([...across, filter]);
    setText("");
  };

  if (tiles.length === 0) return null;

  return (
    <div className="across">
      <span className="across__head">Across every tile</span>

      {across.length === 0 ? null : (
        <ul className="across__chips">
          {across.map((filter, at) => (
            <li key={`${describe(filter)}-${at}`} className="across__chip" title={titleOf(filter, tiles)}>
              <span className="across__said">{describe(filter)}</span>
              <span className="across__reach">{counted(reach(tiles, filter), "tile")}</span>
              <button
                className="across__off"
                onClick={() => onChange(across.filter((_, index) => index !== at))}
                aria-label={`Remove the filter ${describe(filter)}`}
              >
                &times;
              </button>
            </li>
          ))}
        </ul>
      )}

      {offered.length === 0 ? (
        <p className="dash__note">
          No tile on this dashboard groups by anything, so there is no column every tile
          could be narrowed by. A filter across a dashboard is about a dimension its charts
          already share.
        </p>
      ) : (
        <div className="across__form">
          <select
            className="across__field"
            aria-label="Column to filter every tile by"
            value={about}
            onChange={(event) => setColumn(event.target.value)}
          >
            {offered.map((each) => (
              <option key={each} value={each}>
                {each}
              </option>
            ))}
          </select>
          <select
            className="across__field"
            aria-label="Operator"
            value={op}
            onChange={(event) => setOp(event.target.value as typeof op)}
          >
            {CHOSEN.map((each) => (
              <option key={each} value={each}>
                {each}
              </option>
            ))}
          </select>
          {needsValue && temporal ? (
            <select
              className="across__field"
              aria-label="What kind of date"
              value={kind}
              onChange={(event) => setKind(event.target.value as typeof kind)}
            >
              <option value="typed">a date I type</option>
              <option value="today">today</option>
              <option value="start_of">the start of this</option>
              <option value="ago">ago</option>
            </select>
          ) : null}
          {needsValue && relative !== "typed" && relative !== "today" ? (
            <select
              className="across__field"
              aria-label="Unit"
              value={unit}
              onChange={(event) => setUnit(event.target.value as typeof unit)}
            >
              {["year", "quarter", "month", "week", "day"].map((each) => (
                <option key={each} value={each}>
                  {each}
                </option>
              ))}
            </select>
          ) : null}
          {needsValue && relative !== "today" && relative !== "start_of" ? (
            <input
              className="across__field"
              aria-label={relative === "ago" ? "How many" : "Value"}
              value={text}
              onChange={(event) => setText(event.target.value)}
              placeholder={relative === "ago" ? "how many" : quantitative ? "a number" : "a value"}
              inputMode={relative === "ago" || quantitative ? "numeric" : "text"}
            />
          ) : null}
          <button className="btn btn--small" onClick={add} disabled={!addable}>
            Add
          </button>
          {across.length < FILTER_LIMIT ? null : (
            <span className="across__reach">{FILTER_LIMIT} is as many as a query can carry</span>
          )}
        </div>
      )}

      <p className="across__why">
        A filter here is applied to a tile's spec when the tile runs, and never written into
        it. A tile whose query does not read the column is left as it was and says so, since
        a join nobody confirmed is the one thing this will not guess.
      </p>
    </div>
  );
}

/** The value the form is describing, or nothing where it does not describe one yet. A
 * number column parses what was typed rather than sending the string: the grammar allows
 * either, and `total >= "500"` compares a number with text in most warehouses and refuses
 * in the rest. */
function made(
  relative: "typed" | "today" | "start_of" | "ago",
  unit: string,
  text: string,
  quantitative: boolean,
): unknown {
  if (relative === "today") return { relative: "today" };
  if (relative === "start_of") return { relative: "start_of", unit };
  if (relative === "ago") {
    const count = Number(text);
    return text.trim() !== "" && Number.isInteger(count) && count >= 1
      ? { relative: "ago", unit, count }
      : undefined;
  }
  if (text.trim() === "") return undefined;
  if (!quantitative) return text;
  const number = Number(text);
  return Number.isFinite(number) ? number : undefined;
}

/** What the chip says on hover: the column in full, and which tiles it does not reach. The
 * chip itself shows the last segment, because a chip four segments wide pushes the next one
 * off the row. */
function titleOf(filter: Filter, tiles: Tile[]): string {
  const reached = reach(tiles, filter);
  const columns = ("any" in filter ? filter.any : [filter]).map((each) => each.column).join(", ");
  return reached === tiles.length
    ? `${columns} — every tile`
    : `${columns} — ${reached} of ${tiles.length} tiles read that table; the rest are drawn as they were`;
}
