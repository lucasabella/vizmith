import { useState } from "react";
import { Refused, getJoinPath } from "../api";
import {
  FNS,
  UNITS,
  WELLS,
  WellRefusal,
  type Draft,
  type Field,
  type Filter,
  type Fn,
  type Join,
  type Unit,
  type Well,
  anyOf,
  clear,
  inQuery,
  nameOf,
  place,
  ranksRequired,
  reaggregate,
  retop,
  retruncate,
  windowFor,
} from "../spec/spec";

/**
 * The wells, and what dropping a column into one does to the spec.
 *
 * A drop rewrites the spec and sends it down the same path a model's answer takes:
 * validate, then run. Nothing here decides whether the result is legal. A browser that
 * judged its own drop would be a second opinion that can disagree with the one that
 * counts, and the whole design rests on the validator being the only judge.
 *
 * A column on a table the query does not read needs a join, and a join comes from the
 * resolver walking confirmed relationships. It is never guessed and never asked of the
 * model. A pair with no path is refused under the well, naming both tables, with the way
 * to the screen where a relationship is confirmed.
 *
 * Editing a well and editing the JSON are two views of one spec, so a change in either
 * shows in the other. A spec somebody typed that the wells cannot represent is shown as
 * what it is rather than overwritten by them.
 */
export default function Wells({
  draft,
  dragging,
  onChange,
  onDrag,
  onRelationships,
}: {
  draft: Draft | null;
  dragging: Field | null;
  onChange: (draft: Draft) => void;
  onDrag: (field: Field | null) => void;
  onRelationships: () => void;
}) {
  const [over, setOver] = useState<Well | null>(null);
  const [refusal, setRefusal] = useState<{ well: Well; lines: string[]; path: boolean } | null>(null);

  const drop = async (well: Well) => {
    setOver(null);
    setRefusal(null);
    const field = dragging;
    if (field === null) return;

    let joins: Join[] = [];
    if (draft !== null && !inQuery(draft.query, field)) {
      try {
        joins = (await getJoinPath(draft.query.from, field.table)).joins;
      } catch (error) {
        const lines = error instanceof Refused ? error.errors : [(error as Error).message];
        setRefusal({ well, lines, path: true });
        return;
      }
    }
    try {
      onChange(place(draft, well, field, joins));
      // Put down what was picked up. A mouse drop clears this on `dragEnd` anyway; a
      // placement made from the keyboard has no such moment, and a field still held after
      // it landed is a well that places it again on the next Return.
      onDrag(null);
    } catch (error) {
      if (!(error instanceof WellRefusal)) throw error;
      // Still held, on purpose: the drop was refused and the next thing somebody does is
      // try another well, which they cannot do having been made to pick it up again.
      setRefusal({ well, lines: [error.message], path: false });
    }
  };

  const zone = (well: Well) => ({
    onDragOver: (event: React.DragEvent) => {
      event.preventDefault();
      setOver(well);
    },
    onDragLeave: () => setOver(over === well ? null : over),
    onDrop: (event: React.DragEvent) => {
      event.preventDefault();
      void drop(well);
    },
    // The keyboard's half of the same gesture. A well is a button, so Return and Space
    // already reach this; what is left is the way out, because a field picked up with no
    // way to put it down is a mode somebody is stuck in.
    onClick: () => void drop(well),
    onKeyDown: (event: React.KeyboardEvent) => {
      if (event.key === "Escape") onDrag(null);
    },
  });

  const required = draft !== null && ranksRequired(draft);
  const missing = required && draft.query.limit_by === undefined;

  return (
    <div className="wells">
      {WELLS.map((well) => (
        <div key={well} className="well">
          <span className="well__name">
            {well}
            {well === "Top N" && required ? (
              <span className={missing ? "well__tag well__tag--missing" : "well__tag"}>
                {missing ? "Missing" : "Required"}
              </span>
            ) : null}
          </span>

          {well === "Axis" || well === "Legend" ? (
            <Channel
              draft={draft}
              channel={well === "Axis" ? "x" : "color"}
              onChange={onChange}
              well={well}
              over={over}
              holding={dragging}
              zone={zone}
            />
          ) : null}

          {well === "Values" ? (
            <div className="well__slot">
              {/* The null is shed by the guard rather than by a cast. `encoding?.y` alone
                  told the checker nothing about `draft`, so the three controls under here
                  asserted it away — an assertion about a null in the middle of a file whose
                  other casts were about a type. */}
              {draft !== null && draft.chart.encoding.y ? (
                <span className="chip">
                  <Measure draft={draft} onChange={onChange} />
                  <span className="chip__name">{draft.chart.encoding.y.field}</span>
                  <button
                    className="chip__off"
                    onClick={() => onChange(clear(draft, "Values"))}
                    aria-label="Remove from Values"
                  >
                    ×
                  </button>
                </span>
              ) : (
                <Zone well={well} over={over} holding={dragging} zone={zone} />
              )}
            </div>
          ) : null}

          {well === "Top N" ? (
            <div className="well__slot">
              {draft?.query.limit_by ? (
                <span className="chip">
                  <span className="chip__name">
                    {draft.query.limit_by.column} by {draft.query.limit_by.by}
                  </span>
                  <input
                    className="chip__number"
                    type="number"
                    min={1}
                    max={1000}
                    value={draft.query.limit_by.limit}
                    onChange={(event) => onChange(retop(draft, Number(event.target.value) || 1))}
                    aria-label="How many"
                  />
                  <button
                    className="chip__off"
                    onClick={() => onChange(clear(draft, "Top N"))}
                    aria-label="Remove from Top N"
                  >
                    ×
                  </button>
                </span>
              ) : (
                <Zone well={well} over={over} holding={dragging} zone={zone} missing={missing} />
              )}
            </div>
          ) : null}

          {well === "Filters" ? (
            <div className="well__slot">
              {draft === null
                ? null
                : (draft.query.filters ?? []).map(chip).map(({ name, said }, at) => (
                    <span key={`${name}-${at}`} className="chip">
                      <span className="chip__name">{name}</span>
                      <span className="chip__said">{said}</span>
                      <button
                        className="chip__off"
                        onClick={() => onChange(clear(draft, "Filters", at))}
                        aria-label="Remove from Filters"
                      >
                        ×
                      </button>
                    </span>
                  ))}
              <Zone well={well} over={over} holding={dragging} zone={zone} />
            </div>
          ) : null}

          {refusal?.well === well ? (
            // `alert` rather than a polite region: this arrives in response to a drop
            // rather than to a click, so there is nothing to move focus to and nothing
            // else on screen changed. A cross-table drop with no confirmed join path
            // lands here, and silently refusing it reads as a drop that did nothing.
            <div className="well__refusal" role="alert">
              {refusal.lines.map((line) => (
                <p key={line} className="well__refusal-line">
                  {line}
                </p>
              ))}
              {refusal.path ? (
                <button className="well__link" onClick={onRelationships}>
                  Confirm a relationship
                </button>
              ) : null}
            </div>
          ) : null}
        </div>
      ))}

      {draft === null ? (
        <p className="wells__note">
          Drag a column from Fields into a well, or pick one up from its row and place it in
          the well you want. What a well infers, an aggregate or a date unit, is shown in
          the well and can be changed there.
        </p>
      ) : null}

      {/* What is held, for somebody who cannot see that a row has gone into its pressed
          state. Silent otherwise: a region that says something on every drag is a region
          that gets turned off. */}
      <p className="visually-hidden" role="status">
        {dragging === null
          ? ""
          : `Holding ${dragging.column}. Choose a well to place it in, or press Escape.`}
      </p>
    </div>
  );
}

/**
 * What a filter chip reads. A condition is its column and its operator, which is the whole
 * of it. A disjunction is every column it mentions, joined by the word the grammar joins
 * them with, and the count rather than the operators: `status = or total >` on one chip
 * reads as two filters, and three of them read as a chip nobody can parse.
 *
 * The well's job here is to say which columns are narrowing the rows and that one of these
 * chips is the loose kind. What each condition actually says is in `{ } JSON`, which is the
 * other view of the same spec and the one that shows a filter in full. A disjunction is not
 * something a drop can build — a drop writes `is_not_null` — so this is the reading half of
 * the feature, and the chip's × still takes the whole filter out.
 */
function chip(filter: Filter): { name: string; said: string } {
  if (!anyOf(filter)) return { name: short(filter.column), said: filter.op.replace(/_/g, " ") };
  const columns = [...new Set(filter.any.map((condition) => short(condition.column)))];
  return { name: columns.join(" or "), said: `any of ${filter.any.length}` };
}

const short = (column: string): string => column.split(".").slice(-1)[0];

/** A well holding a dimension: the column, and the unit a date is truncated to, which is
 * the other thing a drop infers and therefore the other thing that has to be visible. */
function Channel({
  draft,
  channel,
  well,
  over,
  holding,
  zone,
  onChange,
}: {
  draft: Draft | null;
  channel: "x" | "color";
  well: Well;
  over: Well | null;
  holding: Field | null;
  zone: (well: Well) => object;
  onChange: (draft: Draft) => void;
}) {
  const bound = draft?.chart.encoding[channel];
  if (draft === undefined || draft === null || bound === undefined) {
    return (
      <div className="well__slot">
        <Zone well={well} over={over} holding={holding} zone={zone} />
      </div>
    );
  }

  const item = (draft.query.group_by ?? []).find(
    (each) => nameOf(each) === bound.field,
  );

  return (
    <div className="well__slot">
      <span className="chip">
        <span className="chip__name">{bound.field}</span>
        {bound.type === "temporal" ? (
          <select
            className="chip__pick"
            value={item?.truncate ?? "none"}
            onChange={(event) =>
              onChange(
                retruncate(
                  draft,
                  channel,
                  event.target.value === "none" ? null : (event.target.value as Unit),
                ),
              )
            }
            aria-label="Date unit"
          >
            <option value="none">every value</option>
            {UNITS.map((unit) => (
              <option key={unit} value={unit}>
                per {unit}
              </option>
            ))}
          </select>
        ) : null}
        <button
          className="chip__off"
          onClick={() => onChange(clear(draft, well))}
          aria-label={`Remove from ${well}`}
        >
          ×
        </button>
      </span>
    </div>
  );
}

/**
 * A well's empty slot: where a drop lands, and the keyboard's way to the same place.
 *
 * It was a `div` with drag handlers on it, which is the whole of #143 in one element — the
 * primary interaction of the product was reachable with a mouse and with nothing else. A
 * button is reachable by both, and the drag handlers are unchanged, so this is an input
 * path rather than a second way of placing a field: what it presses is the same `drop`.
 *
 * What it says depends on whether something is held, because a control whose label is
 * "drop a field here" is describing a gesture the keyboard does not have. The visible text
 * is inside the accessible name rather than replaced by it, so somebody driving this by
 * voice can say what they can see.
 */
function Zone({
  well,
  over,
  holding,
  zone,
  missing = false,
}: {
  well: Well;
  over: Well | null;
  holding: Field | null;
  zone: (well: Well) => object;
  missing?: boolean;
}) {
  const said = holding === null ? "Drop a field here" : `Place ${holding.column}`;
  return (
    <button
      className={dropClass(over === well, missing)}
      {...zone(well)}
      aria-label={holding === null ? `${said}, ${well}` : `${said} in ${well}`}
    >
      {said}
    </button>
  );
}

const dropClass = (over: boolean, missing: boolean): string =>
  ["well__drop", over ? "well__drop--over" : "", missing ? "well__drop--missing" : ""]
    .filter(Boolean)
    .join(" ");

/**
 * What the measure on screen is: the aggregate it was taken with, or the window it was read
 * with where a window is what the chart is drawing.
 *
 * The select is the aggregate's, and it is not offered for a window. A window is taken over
 * an aggregate rather than instead of one — `running_total` of a `sum` — so a select here
 * would be changing something two steps behind what the well names, and the one it used to
 * show was `sum` whatever the window said, because no aggregate carried the drawn column's
 * alias. A well that names an inference nobody made is the failure this panel exists to
 * prevent. The window is named instead, and it is changed in `{ } JSON`: nothing in the
 * browser writes a window, the same way nothing in it writes a computed column.
 */
function Measure({ draft, onChange }: { draft: Draft; onChange: (draft: Draft) => void }) {
  const field = draft.chart.encoding.y?.field;
  // Not called `window`, which is a global this file could reasonably want one day.
  const read = windowFor(draft, field);
  if (read !== undefined) {
    return <span className="chip__said">{`${read.fn} of ${read.of}`}</span>;
  }
  return (
    <select
      className="chip__pick"
      value={aggregateOf(draft, field)}
      onChange={(event) => onChange(reaggregate(draft, event.target.value as Fn))}
      aria-label="Aggregate"
    >
      {FNS.map((fn) => (
        <option key={fn} value={fn}>
          {fn}
        </option>
      ))}
    </select>
  );
}

/** The function the measure on screen was taken with, read off the query rather than
 * remembered, because the JSON is the other view of the same spec and may have changed
 * it. */
function aggregateOf(draft: Draft, field: string | undefined): Fn {
  const aggregate = (draft.query.aggregates ?? []).find((each) => each.as === field);
  return aggregate?.fn ?? "sum";
}
