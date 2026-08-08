import { useEffect, useState } from "react";
import { deleteDashboard, execute, getDashboard, getDashboards, saveDashboard } from "../api";
import Chart from "../chart/Deferred";
import type { Row } from "../chart/option";
import { overSeriesLimit } from "../chart/option";
import { refusal, type Refusal } from "../outcome";
import {
  COLUMNS,
  NOTHING,
  TILE_LIMIT,
  add,
  editingIndex,
  move,
  nameProblem,
  opened,
  remove,
  renameable,
  tileTitle,
  widen,
  type Arrangement,
  type Saved,
  type Tile,
} from "../dashboard/dashboard";
import { drawable, type Draft, type Spec } from "../spec/spec";
import { counted } from "../counted";

/**
 * Dashboards: several specs under one name, arranged, and opened again.
 *
 * A tile is a spec and nothing else, so what is on screen here is drawn the same way the
 * single chart is: each tile runs its own spec through `/api/execute` and hands the rows
 * to the same renderer. No rows are stored and none are shared between tiles, which means
 * a tile that fails fails on its own and the rest of the dashboard still draws.
 *
 * The arrangement is an order and a width. Both are controls a person can see, for the
 * same reason a well shows the aggregate it inferred: a layout that cannot be seen and
 * cannot be changed is the quiet kind of wrong this project exists to avoid.
 *
 * A tile is corrected where it was made, in the Chart view, because that is where the
 * wells and `{ } JSON` are and a second editor here would be a second thing to keep true.
 * Opening one hands its spec to that view and marks the tile it came from; what comes back
 * replaces that tile and nothing else.
 */
export default function Dashboards({
  current,
  arrangement,
  onChange,
  onEdit,
}: {
  current: Draft | null;
  arrangement: Arrangement;
  onChange: (arrangement: Arrangement) => void;
  onEdit: (tile: Tile) => void;
}) {
  const [list, setList] = useState<Saved[] | null>(null);
  // What the last save or open said, read the way every other refusal in this interface is
  // read. A save is refused by the validator about as often as by anything else — a tile
  // whose spec no longer passes the rules is the usual way — and this panel used to head
  // that list "What the server said" and stop there.
  const [refused, setRefused] = useState<Refusal | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [working, setWorking] = useState(false);

  // The dashboard being arranged lives in the application rather than here, because
  // adding the chart on screen means leaving this view to build the next one, and state
  // that belongs to a view is state that is thrown away when the view is.
  const { name, tiles } = arrangement;
  const setName = (next: string) => onChange({ ...arrangement, name: next });
  const setTiles = (next: Tile[]) => onChange({ ...arrangement, tiles: next });
  const beingEdited = editingIndex(arrangement);

  const read = () => {
    getDashboards()
      .then((body) => setList(body.dashboards))
      .catch((error: unknown) => setRefused(refusal(error)));
  };

  useEffect(read, []);

  const open = async (opening: string) => {
    setWorking(true);
    try {
      onChange(opened(await getDashboard(opening)));
      setRefused(null);
      setNote(null);
    } catch (error) {
      setRefused(refusal(error));
    } finally {
      setWorking(false);
    }
  };

  const save = async () => {
    const problem = nameProblem(name);
    if (problem !== null) {
      // Refused here, before anything was sent, which is why it does not go through
      // `refusal`: that reads what a server said, and no server was asked.
      setRefused({
        kind: "refused",
        heading: "What the name rule said",
        lines: [problem],
        plain: "The name is checked before the dashboard is sent, so nothing was saved.",
      });
      return;
    }
    setWorking(true);
    try {
      const stored = await saveDashboard(name.trim(), tiles);
      // The name the store settled on, and the tiles that are already on screen. Taking the
      // stored tiles back would mint new ids for tiles that did not change, and every one of
      // them would run its query again for a save that drew nothing new.
      onChange({ ...arrangement, name: stored.name, savedAs: stored.name });
      setRefused(null);
      setNote(`Saved as ${stored.name}.`);
      read();
    } catch (error) {
      setRefused(refusal(error));
    } finally {
      setWorking(false);
    }
  };

  /**
   * Save under the name in the field and forget the old one, which is what renaming is
   * when the name is the identity. In that order: a delete that went first would leave
   * nothing behind if the save were refused.
   */
  const rename = async () => {
    const from = arrangement.savedAs;
    if (from === null) return;
    setWorking(true);
    try {
      const stored = await saveDashboard(name.trim(), tiles);
      await deleteDashboard(from);
      onChange({ ...arrangement, name: stored.name, savedAs: stored.name });
      setRefused(null);
      setNote(`Renamed ${from} to ${stored.name}.`);
      read();
    } catch (error) {
      setRefused(refusal(error));
    } finally {
      setWorking(false);
    }
  };

  const forget = async (forgetting: string) => {
    setWorking(true);
    try {
      await deleteDashboard(forgetting);
      if (forgetting === arrangement.savedAs) onChange(NOTHING);
      setNote(`Deleted ${forgetting}.`);
      read();
    } catch (error) {
      setRefused(refusal(error));
    } finally {
      setWorking(false);
    }
  };

  /** The spec on screen, added as a tile. It is not saved by adding it: a person who
   * arranges four tiles and closes the tab has saved nothing, and a Save button that was
   * pressed is the only thing that says otherwise. */
  const addCurrent = () => {
    if (current === null || !drawable(current)) return;
    setTiles(add(tiles, current));
    setNote(null);
  };

  const addable = current !== null && drawable(current) && tiles.length < TILE_LIMIT;

  return (
    <div className="dash">
      <div className="dash__head">
        <h1 className="dash__title">Dashboards</h1>
        <p className="dash__lead">
          A dashboard is a set of specs under a name. Each tile runs its own spec against the
          source when the dashboard is opened, so a tile shows what the data says now rather
          than what it said when it was saved.
        </p>
      </div>

      <div className="dash__body">
        <aside className="dash__saved">
          <h2 className="dash__sub">Saved</h2>
          {list === null ? (
            <p className="dash__note">Reading what is saved.</p>
          ) : list.length === 0 ? (
            <p className="dash__empty">
              Nothing is saved yet. Build a chart, then add it to a dashboard and save it
              under a name.
            </p>
          ) : (
            <ul className="dash__list">
              {list.map((entry) => (
                <li key={entry.name} className={entry.name === name ? "dash__item dash__item--on" : "dash__item"}>
                  <button className="dash__open" onClick={() => open(entry.name)} disabled={working}>
                    <span className="dash__name">{entry.name}</span>
                    <span className="dash__tally">{counted(entry.tiles, "tile")}</span>
                  </button>
                  <button
                    className="btn btn--quiet"
                    onClick={() => forget(entry.name)}
                    disabled={working}
                  >
                    Delete
                  </button>
                </li>
              ))}
            </ul>
          )}
        </aside>

        <section className="dash__editor">
          <div className="dash__bar">
            <input
              className="dash__field"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Name this dashboard"
              aria-label="Dashboard name"
            />
            <button className="btn" onClick={save} disabled={working || tiles.length === 0}>
              {working ? "Working" : "Save"}
            </button>
            {renameable(arrangement) ? (
              <button className="btn btn--quiet" onClick={rename} disabled={working}>
                Rename {arrangement.savedAs}
              </button>
            ) : null}
            <button className="btn btn--quiet" onClick={addCurrent} disabled={!addable}>
              Add the chart on screen
            </button>
            <span className="dash__count">
              {tiles.length} of {TILE_LIMIT} tiles
            </span>
          </div>

          {current === null || drawable(current) ? null : (
            <p className="dash__note">
              The chart on screen has no measure yet, so there is nothing to add. Finish it in
              the Visualisation panel first.
            </p>
          )}

          {/* Two regions rather than one, because a refusal and a confirmation are not the
              same urgency, and they were equally silent. A refused save is an alert: it
              interrupts, because nothing moved focus and a save that says nothing reads as
              a save that happened. "Saved as …" is polite, because it is agreement with
              what was just pressed and it can wait for a gap. */}
          <div role="alert">
            {refused === null ? null : (
              <div className="refusal">
                <p className="refusal__head">{refused.heading}</p>
                <ul className="refusal__lines">
                  {refused.lines.map((line) => (
                    <li key={line}>{line}</li>
                  ))}
                </ul>
                <p className="refusal__plain">{refused.plain}</p>
              </div>
            )}
          </div>
          <div role="status" aria-live="polite">
            {refused === null && note !== null ? <p className="dash__note">{note}</p> : null}
          </div>

          {tiles.length === 0 ? (
            <p className="dash__empty">
              No tiles. Open a saved dashboard on the left, or build a chart in the Chart view
              and add it here.
            </p>
          ) : (
            <div className="grid">
              {tiles.map((tile, index) => (
                <article
                  key={tile.id}
                  className={index === beingEdited ? "grid__cell grid__cell--editing" : "grid__cell"}
                  style={{ gridColumn: `span ${Math.min(tile.width, COLUMNS)}` }}
                >
                  <header className="grid__head">
                    <span className="grid__title">{tileTitle(tile, index)}</span>
                    {index === beingEdited ? (
                      <span className="grid__editing">being corrected</span>
                    ) : null}
                    <span className="grid__actions">
                      <button
                        className="grid__btn"
                        onClick={() => onEdit(tile)}
                        aria-label={`Correct ${tileTitle(tile, index)}`}
                        title="Correct this chart"
                      >
                        Edit
                      </button>
                      <button
                        className="grid__btn"
                        onClick={() => setTiles(move(tiles, index, -1))}
                        disabled={index === 0}
                        aria-label={`Move ${tileTitle(tile, index)} earlier`}
                        title="Move earlier"
                      >
                        &larr;
                      </button>
                      <button
                        className="grid__btn"
                        onClick={() => setTiles(move(tiles, index, 1))}
                        disabled={index === tiles.length - 1}
                        aria-label={`Move ${tileTitle(tile, index)} later`}
                        title="Move later"
                      >
                        &rarr;
                      </button>
                      <button
                        className="grid__btn"
                        onClick={() => setTiles(widen(tiles, index, tile.width === COLUMNS ? 1 : COLUMNS))}
                        aria-pressed={tile.width === COLUMNS}
                        aria-label={`Width of ${tileTitle(tile, index)}`}
                        title="Half width or full width"
                      >
                        {tile.width === COLUMNS ? "Full" : "Half"}
                      </button>
                      <button
                        className="grid__btn"
                        onClick={() => setTiles(remove(tiles, index))}
                        aria-label={`Remove ${tileTitle(tile, index)}`}
                        title="Remove"
                      >
                        &times;
                      </button>
                    </span>
                  </header>
                  {/* A tile's own state is not announced. A dashboard holds up to 24 of
                      them and they all resolve at once, so a region per tile is 24
                      sentences for one gesture, which is how a person turns announcements
                      off. What a tile is doing is written in the tile — "Running the
                      spec.", or what refused — and read where it sits. */}
                  <div className="grid__body">
                    <TileChart spec={tile.spec} />
                  </div>
                </article>
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

/**
 * One tile's rows, fetched by the tile itself.
 *
 * Per tile rather than per dashboard, because a dashboard that fetched everything at once
 * would be as slow as its slowest tile and as broken as its worst one. What refused is
 * shown in the tile it refused for, so the other tiles are still readable.
 */
export function TileChart({ spec }: { spec: Spec }) {
  // One piece of state, and the spec it is about. Clearing the previous tile's rows used
  // to be two more setState calls at the top of the effect, which is a cascading render
  // for what is a fetch: the tile drew the old rows, then drew "Running the spec.", then
  // drew the new ones. Keying the answer on the spec says "I have nothing for this one
  // yet" without a second render, and it is what the answer is actually about — a result
  // set that arrived for a spec this tile no longer holds is not this tile's answer.
  const [answer, setAnswer] = useState<{ spec: Spec; rows?: Row[]; refused?: Refusal } | null>(
    null,
  );

  useEffect(() => {
    let live = true;
    execute(spec)
      .then((body) => live && setAnswer({ spec, rows: body.rows }))
      // The same reading of a failure the canvas gets, from the same function. A tile said
      // "What the source said" over the top of every refusal, including the ones no source
      // ever saw: a spec the validator rejected, and a request this server would not spend
      // a query on. Both sent a person to check a warehouse that was never touched.
      .catch((error: unknown) => live && setAnswer({ spec, refused: refusal(error) }));
    return () => {
      live = false;
    };
  }, [spec]);

  const { rows = null, refused = null } = answer?.spec === spec ? answer : {};

  if (refused !== null) {
    return (
      <div className="grid__refusal">
        <p className="refusal__head">{refused.heading}</p>
        <ul className="refusal__lines">
          {refused.lines.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
        {/* The plain sentence, without the "In plain terms" heading the canvas puts above
            it. A tile is half a column wide and holds a chart the rest of the time; the
            sentence is the part that says what to do, and a heading introducing one line
            is the part that does not fit. */}
        <p className="refusal__plain">{refused.plain}</p>
      </div>
    );
  }

  if (rows === null) return <p className="grid__working">Running the spec.</p>;

  const tooMany = overSeriesLimit(spec, rows);
  if (tooMany !== null) {
    return (
      <div className="grid__refusal">
        <p className="refusal__head">What the renderer said</p>
        <p className="refusal__plain">{tooMany}</p>
      </div>
    );
  }

  return <Chart spec={spec} rows={rows} />;
}
