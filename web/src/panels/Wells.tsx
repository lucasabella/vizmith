import { useState } from "react";
import { Refused, getJoinPath } from "../api";
import {
  FNS,
  UNITS,
  WELLS,
  WellRefusal,
  type Draft,
  type Field,
  type Fn,
  type Join,
  type Unit,
  type Well,
  clear,
  inQuery,
  place,
  ranksRequired,
  reaggregate,
  retop,
  retruncate,
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
  onRelationships,
}: {
  draft: Draft | null;
  dragging: Field | null;
  onChange: (draft: Draft) => void;
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
    } catch (error) {
      if (!(error instanceof WellRefusal)) throw error;
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
  });

  const encoding = draft?.chart.encoding;
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
              zone={zone}
            />
          ) : null}

          {well === "Values" ? (
            <div className="well__slot">
              {encoding?.y ? (
                <span className="chip">
                  <select
                    className="chip__pick"
                    value={aggregateOf(draft)}
                    onChange={(event) => onChange(reaggregate(draft as Draft, event.target.value as Fn))}
                    aria-label="Aggregate"
                  >
                    {FNS.map((fn) => (
                      <option key={fn} value={fn}>
                        {fn}
                      </option>
                    ))}
                  </select>
                  <span className="chip__name">{encoding.y.field}</span>
                  <button
                    className="chip__off"
                    onClick={() => onChange(clear(draft as Draft, "Values"))}
                    aria-label="Remove from Values"
                  >
                    ×
                  </button>
                </span>
              ) : (
                <div className={dropClass(over === well, false)} {...zone(well)}>
                  Drop a field here
                </div>
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
                <div className={dropClass(over === well, missing)} {...zone(well)}>
                  Drop a field here
                </div>
              )}
            </div>
          ) : null}

          {well === "Filters" ? (
            <div className="well__slot">
              {(draft?.query.filters ?? []).map((filter, at) => (
                <span key={`${filter.column}-${at}`} className="chip">
                  <span className="chip__name">{filter.column.split(".").slice(-1)[0]}</span>
                  <span className="chip__said">{filter.op.replace(/_/g, " ")}</span>
                  <button
                    className="chip__off"
                    onClick={() => onChange(clear(draft as Draft, "Filters", at))}
                    aria-label="Remove from Filters"
                  >
                    ×
                  </button>
                </span>
              ))}
              <div className={dropClass(over === well, false)} {...zone(well)}>
                Drop a field here
              </div>
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
          Drag a column from Fields into a well. What a well infers, an aggregate or a date
          unit, is shown in the well and can be changed there.
        </p>
      ) : null}
    </div>
  );
}

/** A well holding a dimension: the column, and the unit a date is truncated to, which is
 * the other thing a drop infers and therefore the other thing that has to be visible. */
function Channel({
  draft,
  channel,
  well,
  over,
  zone,
  onChange,
}: {
  draft: Draft | null;
  channel: "x" | "color";
  well: Well;
  over: Well | null;
  zone: (well: Well) => object;
  onChange: (draft: Draft) => void;
}) {
  const bound = draft?.chart.encoding[channel];
  if (draft === undefined || draft === null || bound === undefined) {
    return (
      <div className="well__slot">
        <div className={dropClass(over === well, false)} {...zone(well)}>
          Drop a field here
        </div>
      </div>
    );
  }

  const item = (draft.query.group_by ?? []).find(
    (each) => (each.as ?? each.column.split(".").slice(-1)[0]) === bound.field,
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

const dropClass = (over: boolean, missing: boolean): string =>
  ["well__drop", over ? "well__drop--over" : "", missing ? "well__drop--missing" : ""]
    .filter(Boolean)
    .join(" ");

/** The function the measure on screen was taken with, read off the query rather than
 * remembered, because the JSON is the other view of the same spec and may have changed
 * it. */
function aggregateOf(draft: Draft | null): Fn {
  const measure = draft?.chart.encoding.y?.field;
  const aggregate = (draft?.query.aggregates ?? []).find((each) => each.as === measure);
  return aggregate?.fn ?? "sum";
}
