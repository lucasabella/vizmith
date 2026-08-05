import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { Spec } from "../chart/option";
import Dashboards from "../views/Dashboards";
import {
  COLUMNS,
  TILE_LIMIT,
  add,
  move,
  nameProblem,
  remove,
  tileTitle,
  widen,
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
  titles.map((title) => ({ spec: spec(title), width: 1 }));

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
const drawn = (current: Spec | null = null, arranged: Tile[] = []) =>
  renderToStaticMarkup(
    <Dashboards
      current={current as never}
      dashboard={{ name: "", tiles: arranged }}
      onChange={() => {}}
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
});
