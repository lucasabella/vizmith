import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { Spec } from "../chart/option";
import Dashboards from "../views/Dashboards";
import {
  COLUMNS,
  NOTHING,
  TILE_LIMIT,
  add,
  editingIndex,
  move,
  nameProblem,
  opened,
  putBack,
  asStored,
  remove,
  renameable,
  tileTitle,
  tiled,
  widen,
  type Arrangement,
  type Tile,
} from "./dashboard";

const spec = (title?: string): Spec => ({
  title,
  chart: {
    mark: "bar",
    encoding: {
      x: { field: "country", type: "nominal" },
      y: { field: "revenue", type: "quantitative" },
    },
  },
});

const tiles = (...titles: (string | undefined)[]): Tile[] =>
  titles.map((title) => tiled(spec(title)));

describe("arranging a dashboard", () => {
  it("adds a tile at the end, where a chart that was just made belongs", () => {
    const arranged = add(tiles("first"), spec("second"));

    expect(arranged.map((tile) => tile.spec.title)).toEqual(["first", "second"]);
    expect(arranged[1].width).toBe(1);
  });

  it("leaves the list it was given alone", () => {
    const before = tiles("first");

    add(before, spec("second"));
    move(before, 0, 1);
    remove(before, 0);
    widen(before, 0, COLUMNS);

    expect(before.map((tile) => tile.spec.title)).toEqual(["first"]);
    expect(before[0].width).toBe(1);
  });

  it("moves a tile one step either way", () => {
    const arranged = move(tiles("a", "b", "c"), 1, -1);

    expect(arranged.map((tile) => tile.spec.title)).toEqual(["b", "a", "c"]);
    expect(move(arranged, 0, 1).map((tile) => tile.spec.title)).toEqual(["a", "b", "c"]);
  });

  it("does nothing at either end rather than wrapping round", () => {
    const arranged = tiles("a", "b");

    expect(move(arranged, 0, -1)).toBe(arranged);
    expect(move(arranged, 1, 1)).toBe(arranged);
  });

  it("removes the tile that was asked for and nothing else", () => {
    expect(remove(tiles("a", "b", "c"), 1).map((tile) => tile.spec.title)).toEqual(["a", "c"]);
  });

  it("widens one tile without touching the others", () => {
    const arranged = widen(tiles("a", "b"), 1, COLUMNS);

    expect(arranged.map((tile) => tile.width)).toEqual([1, COLUMNS]);
  });

  it("refuses a width the grid does not have, since the store would refuse the save", () => {
    const arranged = tiles("a");

    expect(widen(arranged, 0, 0)).toBe(arranged);
    expect(widen(arranged, 0, COLUMNS + 1)).toBe(arranged);
  });

  it("keeps a tile's identity when it moves, so the tile moves and no query runs", () => {
    // Keyed by position, React hands the two components each other's spec and both run
    // their query again for a gesture that changed no data. The id is what moves with the
    // tile, and what the grid keys on.
    const arranged = tiles("a", "b", "c");
    const moved = move(arranged, 1, -1);

    expect(moved.map((tile) => tile.id)).toEqual([
      arranged[1].id,
      arranged[0].id,
      arranged[2].id,
    ]);
    expect(moved[0].spec).toBe(arranged[1].spec);
  });

  it("keeps the same spec object through a width change and a removal", () => {
    // The effect that runs a tile's query is keyed on its spec, so an equal-but-new spec
    // would be a statement on the warehouse for a layout gesture.
    const arranged = tiles("a", "b");
    const wide = widen(arranged, 0, COLUMNS);

    expect(wide[0].spec).toBe(arranged[0].spec);
    expect(wide[0].id).toBe(arranged[0].id);
    expect(remove(arranged, 0)[0].spec).toBe(arranged[1].spec);
    expect(remove(arranged, 0)[0].id).toBe(arranged[1].id);
  });

  it("gives every tile an identity of its own, equal specs included", () => {
    const two = [tiled(spec("same")), tiled(spec("same"))];

    expect(two[0].id).not.toBe(two[1].id);
  });

  it("sends the store a spec and a width and nothing this browser invented", () => {
    expect(asStored(tiles("a", "b"))).toEqual([
      { spec: spec("a"), width: 1 },
      { spec: spec("b"), width: 1 },
    ]);
    expect(asStored(tiles("a"))[0]).not.toHaveProperty("id");
  });

  it("gives the tiles of a dashboard it opened an identity each", () => {
    const arrangement = opened({ name: "Trade", tiles: [{ spec: spec("a"), width: 1 }] });

    expect(arrangement.tiles[0].id).toBeTruthy();
    expect(arrangement.tiles[0].spec.title).toBe("a");
    expect(arrangement.savedAs).toBe("Trade");
  });

  it("calls a tile by its spec's title, and by where it sits where it has none", () => {
    expect(tileTitle(tiles("revenue by country")[0], 0)).toBe("revenue by country");
    expect(tileTitle(tiles(undefined)[0], 2)).toBe("Tile 3");
  });
});

describe("a dashboard name", () => {
  it("has to be one", () => {
    expect(nameProblem("")).not.toBeNull();
    expect(nameProblem("   ")).not.toBeNull();
  });

  it("cannot hold what addresses it", () => {
    expect(nameProblem("Revenue/2026")).toContain("slash");
  });

  it("is bounded, so a heading stays a heading", () => {
    expect(nameProblem("a".repeat(81))).toContain("80");
  });

  it("passes what a person would actually type", () => {
    expect(nameProblem("Revenue, 2026")).toBeNull();
    expect(nameProblem("  Revenue  ")).toBeNull();
  });
});

/** A static render, which is what these tests reach: the effects that read the server do
 * not run, so what is asserted is the state before anything came back. */
const drawn = (current: Spec | null = null, arranged: Tile[] = [], over: Partial<Arrangement> = {}) =>
  renderToStaticMarkup(
    <Dashboards
      current={current as never}
      arrangement={{ ...NOTHING, tiles: arranged, ...over }}
      onChange={() => {}}
      onEdit={() => {}}
    />,
  );

describe("the dashboards view", () => {
  it("says what a dashboard is before anything is saved", () => {
    expect(drawn()).toContain("Reading what is saved");
  });

  it("says there are no tiles rather than showing an empty grid", () => {
    expect(drawn()).toContain("No tiles");
  });

  it("cannot be saved with nothing in it", () => {
    // The Save button is disabled at zero tiles, which is the same rule the store holds.
    expect(drawn()).toContain("disabled");
  });

  it("says how many tiles a dashboard may hold, since opening one is a statement each", () => {
    expect(drawn()).toContain(`of ${TILE_LIMIT} tiles`);
  });

  it("will not add a chart that has no measure yet, and says why", () => {
    const half = { chart: { mark: "bar", encoding: { x: { field: "country", type: "nominal" } } } };

    expect(drawn(half as unknown as Spec)).toContain("no measure yet");
  });

  it("draws a tile per spec, titled and with the controls that arrange it", () => {
    const markup = drawn(null, tiles("revenue by country", undefined));

    expect(markup).toContain("revenue by country");
    expect(markup).toContain("Tile 2");
    expect(markup).toContain("Move later");
    expect(markup).toContain("span 1");
  });

  it("spans a widened tile across the grid", () => {
    expect(drawn(null, widen(tiles("wide"), 0, COLUMNS))).toContain(`span ${COLUMNS}`);
  });

  it("keeps the arrangement out of the view, so leaving it does not lose one", () => {
    // The tiles come in as a prop and go out through onChange. A view that held them
    // would drop them the moment somebody went back to build the next chart.
    expect(drawn(null, tiles("a"))).toContain("Running the spec");
  });

  it("has somewhere to announce a save and somewhere else to announce a refusal", () => {
    // Both were drawn and neither was announced, which made a save that succeeded and a
    // save that was refused equally silent.
    //
    // Containers that are in the tree from the start, because the polite one has to be: a
    // live region is reported when its contents change while it is in the document, and one
    // swapped in already carrying its sentence is one a reader may never mention. An alert
    // is the exception — inserting a `role="alert"` node is the pattern readers do announce,
    // which is why `Wells` puts one under the well that refused rather than five empty ones
    // under every well.
    const markup = drawn();

    expect(markup).toContain('role="alert"');
    expect(markup).toContain('role="status" aria-live="polite"');
  });
});

describe("correcting a tile", () => {
  const arrangement = (over: Partial<Arrangement> = {}): Arrangement => ({
    ...NOTHING,
    name: "Trade",
    savedAs: "Trade",
    tiles: tiles("a", "b", "c"),
    ...over,
  });

  it("knows where the tile being corrected sits", () => {
    const one = arrangement();

    expect(editingIndex({ ...one, editing: one.tiles[1] })).toBe(1);
    expect(editingIndex(one)).toBe(-1);
  });

  it("follows the tile rather than the position when the list is reordered", () => {
    // The failure this exists to prevent: a position would go on pointing at slot 1 after
    // the tile moved, and the correction would land in a different chart. Both draw, so
    // nothing would say so.
    const one = arrangement();
    const editing = one.tiles[1];
    const reordered = { ...one, tiles: move(one.tiles, 1, -1), editing };

    expect(editingIndex(reordered)).toBe(0);
    expect(putBack(reordered, spec("corrected")).tiles.map((tile) => tile.spec.title)).toEqual([
      "corrected",
      "a",
      "c",
    ]);
  });

  it("puts the correction back in place, keeping the tile's width", () => {
    const one = arrangement({ tiles: widen(tiles("a", "b"), 1, COLUMNS) });
    const put = putBack({ ...one, editing: one.tiles[1] }, spec("corrected"));

    expect(put.tiles.map((tile) => tile.spec.title)).toEqual(["a", "corrected"]);
    expect(put.tiles[1].width).toBe(COLUMNS);
    expect(put.editing).toBeNull();
  });

  it("does not add back a tile that was removed while it was being corrected", () => {
    const one = arrangement();
    const editing = one.tiles[1];
    const without = { ...one, tiles: remove(one.tiles, 1), editing };

    const put = putBack(without, spec("corrected"));

    expect(put.tiles.map((tile) => tile.spec.title)).toEqual(["a", "c"]);
    expect(put.editing).toBeNull();
  });

  it("marks the tile being corrected on the grid", () => {
    const arranged = tiles("a", "b");

    expect(drawn(null, arranged, { editing: arranged[1] })).toContain("being corrected");
    expect(drawn(null, arranged)).not.toContain("being corrected");
  });

  it("offers to correct every tile", () => {
    expect(drawn(null, tiles("a"))).toContain("Correct a");
  });
});

describe("renaming a dashboard", () => {
  const saved = (over: Partial<Arrangement>): Arrangement => ({
    ...NOTHING,
    name: "Trade",
    savedAs: "Trade",
    tiles: tiles("a"),
    ...over,
  });

  it("is offered once the name in the field is another saveable one", () => {
    expect(renameable(saved({ name: "Trade, 2026" }))).toBe(true);
  });

  it("is not offered for the name it already has, or for one that cannot be saved", () => {
    expect(renameable(saved({}))).toBe(false);
    expect(renameable(saved({ name: "  Trade  " }))).toBe(false);
    expect(renameable(saved({ name: "" }))).toBe(false);
    expect(renameable(saved({ name: "Trade/2026" }))).toBe(false);
  });

  it("is not offered for a dashboard that was never saved, since there is nothing to rename", () => {
    expect(renameable({ ...NOTHING, name: "Trade", tiles: tiles("a") })).toBe(false);
  });

  it("names what it is renaming from, so the button is not a guess", () => {
    expect(drawn(null, tiles("a"), { name: "Trade, 2026", savedAs: "Trade" })).toContain(
      "Rename Trade",
    );
  });

  it("takes a dashboard off the server as the name it was opened under", () => {
    expect(opened({ name: "Trade", tiles: tiles("a") })).toMatchObject({
      savedAs: "Trade",
      editing: null,
    });
  });
});
