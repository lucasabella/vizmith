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

import type { Spec } from "../chart/option";
import type { Draft } from "../spec/spec";

/** Mirrors `COLUMNS` in `dashboards.py`. The grid a dashboard is arranged on. */
export const COLUMNS = 2;

/** Mirrors `TILE_LIMIT`. Opening a dashboard is one statement per tile, so the interface
 * stops offering to add one rather than letting the server refuse the save afterwards. */
export const TILE_LIMIT = 24;

/** Mirrors `NAME_LIMIT`. */
export const NAME_LIMIT = 80;

export type Tile = { spec: Spec; width: number };

export type Dashboard = { name: string; tiles: Tile[] };

/** What the list endpoint answers: a name and how many tiles are under it, never a spec. */
export type Saved = { name: string; tiles: number };

/** A tile added to the end, which is where a chart that was just made belongs: anywhere
 * else would move something a person arranged. */
export const add = (tiles: Tile[], spec: Spec | Draft): Tile[] => [
  ...tiles,
  { spec: spec as Spec, width: 1 },
];

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
export const nameProblem = (name: string): string | null => {
  const trimmed = name.trim();
  if (trimmed === "") return "A dashboard is saved under a name.";
  if (trimmed.length > NAME_LIMIT) return `A name is at most ${NAME_LIMIT} characters.`;
  if (trimmed.includes("/")) return "A name cannot hold a slash, because it is what addresses the dashboard.";
  return null;
};
