/**
 * A dashboard as the interface arranges it.
 *
 * Everything here returns a new list rather than editing one, and nothing here judges a
 * spec: the store validates every tile on the way in, through the same validator a model's
 * answer goes through, and a second opinion in the browser is one that can disagree with
 * the one that counts.
 *
 * The arrangement is an order and a width, and that is all of it. A free canvas of pixel
 * positions would be a stored geometry that is wrong on the next screen, so a tile is one
 * column or the whole width and where it sits is where it sits in the list.
 */

import type { Spec } from "../spec/spec";
import type { Across } from "./across";

/** Mirrors `COLUMNS` in `dashboards.py`. The grid a dashboard is arranged on. */
export const COLUMNS = 2;

/** Mirrors `TILE_LIMIT`. Opening a dashboard is one statement per tile, so the interface
 * stops offering to add one rather than letting the server refuse the save afterwards. */
export const TILE_LIMIT = 24;

/** Mirrors `NAME_LIMIT`. */
export const NAME_LIMIT = 80;

/** Mirrors `maxItems` on `query.filters` in the schema, which is the cap the store applies
 * to a dashboard's filters — they are the same list, judged by the same `$defs`. The bar
 * stops offering to add one rather than letting the save refuse the whole dashboard for a
 * filter somebody could simply not have added. */
export const FILTER_LIMIT = 16;

/**
 * A tile on screen: what the store holds, plus an identity the interface gives it.
 *
 * The id is what React keys the grid on. Keyed by position, moving a tile hands each of
 * the two components the other's spec, and a tile whose spec changed runs its query again
 * — so arranging a dashboard costs a statement per press, for a gesture that changed no
 * data. An id moves with the tile, so the component moves and nothing runs.
 *
 * It is the only identity a tile has, since a spec is a value and two tiles may hold equal
 * ones, and it is what `editing` is found by for the same reason.
 */
export type Tile = { id: string; spec: Spec; width: number };

/** A tile as the store holds it. No id: that is interface state, and the store's shape is
 * a spec and a width and nothing else. */
export type StoredTile = { spec: Spec; width: number };

export type Dashboard = { name: string; tiles: StoredTile[]; filters?: Across };

let minted = 0;

/** A tile with an identity of its own. Minted here rather than derived from the spec,
 * because two tiles may hold equal specs and they are still two tiles. */
export const tiled = (spec: Spec, width = 1): Tile => ({
  id: `tile-${(minted += 1)}`,
  spec,
  width,
});

/** What goes to the store: the spec and the width, in that shape and no other. */
export const asStored = (tiles: Tile[]): StoredTile[] =>
  tiles.map(({ spec, width }) => ({ spec, width }));

/**
 * The dashboard on screen, which is more than the one on the server.
 *
 * `savedAs` is the name it was last opened or saved under, and it is what makes a rename
 * one gesture rather than a save and a delete somebody has to know to do. It is null for a
 * dashboard that has never been saved, where there is nothing to rename.
 *
 * `editing` is the tile whose spec is being corrected in the Chart view, held as the tile
 * itself rather than as a position in the list. A position would go on pointing somewhere
 * after the list was reordered, and the correction would come back into the wrong tile —
 * silently, since both of them draw. A tile that was removed while it was being corrected
 * is then a tile this cannot find, which is a sentence the interface can say.
 */
export type Arrangement = {
  name: string;
  tiles: Tile[];
  savedAs: string | null;
  editing: Tile | null;
  /** The filters that apply across every tile that can take one. Part of the arrangement
   * rather than of any tile: see `across.ts` for why a narrowing of the whole page must not
   * be written into a spec somebody built. */
  across: Across;
};

export const NOTHING: Arrangement = {
  name: "",
  tiles: [],
  savedAs: null,
  editing: null,
  across: [],
};

/** A dashboard as it arrives from the server: saved under its name, nothing being edited,
 * and every tile given the identity the interface arranges it by. */
export const opened = (dashboard: Dashboard): Arrangement => ({
  name: dashboard.name,
  tiles: dashboard.tiles.map((tile) => tiled(tile.spec, tile.width)),
  savedAs: dashboard.name,
  editing: null,
  // Absent from a dashboard saved before one could hold a filter, which is the ordinary
  // case rather than a broken one: no filter across the tiles is what all of them meant.
  across: dashboard.filters ?? [],
});

/** Which tile is being corrected, as a position, or -1 where it is no longer in the list.
 * By id: a reference would be lost the moment something copied the tile, and a position
 * would go on pointing somewhere after the list was reordered. */
export const editingIndex = (arrangement: Arrangement): number =>
  arrangement.editing === null
    ? -1
    : arrangement.tiles.findIndex((tile) => tile.id === arrangement.editing?.id);

/**
 * The corrected spec, back in the tile it came from, keeping that tile's width and its
 * place in the order.
 *
 * A tile that is no longer there is not appended: the person removed it while they were
 * correcting it, and adding it back would undo that without being asked. The arrangement
 * comes back unchanged and the caller says so.
 */
export const putBack = (arrangement: Arrangement, spec: Spec): Arrangement => {
  const at = editingIndex(arrangement);
  if (at === -1) return { ...arrangement, editing: null };
  return {
    ...arrangement,
    tiles: arrangement.tiles.map((tile, index) =>
      index === at ? { ...tile, spec } : tile,
    ),
    editing: null,
  };
};

/** What the list endpoint answers: a name and how many tiles are under it, never a spec. */
export type Saved = { name: string; tiles: number };

/** A tile added to the end, which is where a chart that was just made belongs: anywhere
 * else would move something a person arranged. */
export const add = (tiles: Tile[], spec: Spec): Tile[] => [...tiles, tiled(spec)];

export const remove = (tiles: Tile[], index: number): Tile[] =>
  tiles.filter((_, at) => at !== index);

/** One step earlier or later. A tile at either end does not move, and asking it to is not
 * an error: the control is simply the thing that did nothing. */
export const move = (tiles: Tile[], index: number, by: -1 | 1): Tile[] => {
  const to = index + by;
  if (index < 0 || index >= tiles.length || to < 0 || to >= tiles.length) return tiles;
  const moved = [...tiles];
  [moved[index], moved[to]] = [moved[to], moved[index]];
  return moved;
};

/** Half width or full width. Anything outside the grid is refused by the store, so the
 * interface never offers it. */
export const widen = (tiles: Tile[], index: number, width: number): Tile[] =>
  width < 1 || width > COLUMNS
    ? tiles
    : tiles.map((tile, at) => (at === index ? { ...tile, width } : tile));

/** What a tile is called on screen. The spec's own title where it has one, because that is
 * the title the chart already draws, and its position where it has none: a tile with no
 * name still has to be referred to by the controls that move it. */
export const tileTitle = (tile: Tile, index: number): string =>
  tile.spec.title ?? `Tile ${index + 1}`;

/**
 * Whether a name can be saved, and why not where it cannot.
 *
 * The store is the judge and answers the same questions, so this exists only to keep a
 * person from pressing Save to find out. The wording is this file's rather than the
 * server's, because a message shown next to a field a person is typing in is not the same
 * message as one returned to a client.
 */
/** Whether renaming is on offer: something to rename from, and a new name that could be
 * saved. Typing the same name back is not a rename, and neither is emptying the field. */
export const renameable = (arrangement: Arrangement): boolean =>
  arrangement.savedAs !== null &&
  nameProblem(arrangement.name) === null &&
  arrangement.name.trim() !== arrangement.savedAs;

export const nameProblem = (name: string): string | null => {
  const trimmed = name.trim();
  if (trimmed === "") return "A dashboard is saved under a name.";
  if (trimmed.length > NAME_LIMIT) return `A name is at most ${NAME_LIMIT} characters.`;
  if (trimmed.includes("/")) return "A name cannot hold a slash, because it is what addresses the dashboard.";
  // The store refuses a control character too, and a check that stops short of the judge's
  // rules is a check that lets somebody press Save to find out. `tests/fixtures/mirrors`
  // holds the cases both sides are asked.
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f]/.test(trimmed)) {
    return "A name cannot hold a control character, because it is not something a heading can show.";
  }
  return null;
};
