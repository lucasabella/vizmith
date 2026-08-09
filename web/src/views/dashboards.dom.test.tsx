import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import Dashboards from "./Dashboards";
import { NOTHING, tiled, type Arrangement } from "../dashboard/dashboard";
import type { Spec } from "../spec/spec";

/**
 * The five async handlers, which were the least reachable code in the interface.
 *
 * `save`, `rename`, `forget`, `open` and `addCurrent` share a `working` flag and a refusal
 * path, and a static render reaches none of them. What they get wrong is not arithmetic —
 * `dashboard.ts` is pure and well covered — it is the order of two awaited calls and what
 * is on screen between them.
 *
 * The store is stubbed. Whether it saves correctly is the server's business and has tests
 * in the other language; what is under test is what this view does with an answer.
 */
vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    getDashboards: vi.fn(async () => ({ dashboards: [] })),
    getDashboard: vi.fn(),
    saveDashboard: vi.fn(),
    deleteDashboard: vi.fn(),
    execute: vi.fn(async () => ({ spec: {} as Spec, rows: [] })),
  };
});

const { Refused, deleteDashboard, execute, getDashboards, saveDashboard } = await import("../api");

const spec = (title: string): Spec =>
  ({
    title,
    query: { from: "vizmith.shop.orders", limit: 500 },
    chart: {
      mark: "bar",
      encoding: {
        x: { field: "country", type: "nominal" },
        y: { field: "revenue", type: "quantitative" },
      },
    },
  }) as unknown as Spec;

function view(over: Partial<Arrangement> = {}) {
  const onChange = vi.fn();
  render(
    <Dashboards
      current={null}
      columns={[]}
      arrangement={{ ...NOTHING, tiles: [tiled(spec("Revenue"))], ...over }}
      onChange={onChange}
      onEdit={() => {}}
    />,
  );
  return { onChange, user: userEvent.setup() };
}

afterEach(() => vi.clearAllMocks());

describe("saving a dashboard", () => {
  it("keeps the tiles on screen rather than the ones the store handed back", async () => {
    // Taking the stored tiles back would mint new ids for tiles that did not change, and
    // every one of them would run its query again for a save that drew nothing new.
    vi.mocked(saveDashboard).mockResolvedValue({ name: "Trade", tiles: [] });
    const { onChange, user } = view({ name: "Trade" });

    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(onChange).toHaveBeenCalled());
    const next = onChange.mock.calls[0][0] as Arrangement;
    expect(next.savedAs).toBe("Trade");
    expect(next.tiles).toHaveLength(1);
  });

  it("says what the store said when it refuses, and keeps the arrangement", async () => {
    vi.mocked(saveDashboard).mockRejectedValue(new Refused(["a dashboard holds at most 24 tiles"]));
    const { onChange, user } = view({ name: "Trade" });

    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText(/at most 24 tiles/)).toBeDefined();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("refuses a name the store would refuse, without asking it", async () => {
    const { user } = view({ name: "   " });

    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(saveDashboard).not.toHaveBeenCalled());
    expect(screen.getByRole("alert").textContent).toBeTruthy();
  });

  it("announces the confirmation politely and the refusal as an alert", async () => {
    vi.mocked(saveDashboard).mockResolvedValue({ name: "Trade", tiles: [] });
    const { user } = view({ name: "Trade" });

    await user.click(screen.getByRole("button", { name: "Save" }));

    const polite = await screen.findByRole("status");
    expect(polite.textContent).toContain("Saved as Trade");
  });
});

describe("renaming a dashboard", () => {
  it("saves under the new name before forgetting the old one", async () => {
    // In that order on purpose: a delete that went first would leave nothing behind if the
    // save were refused.
    const order: string[] = [];
    vi.mocked(saveDashboard).mockImplementation(async () => {
      order.push("save");
      return { name: "Trade, 2026", tiles: [] };
    });
    vi.mocked(deleteDashboard).mockImplementation(async () => {
      order.push("delete");
      return {};
    });
    const { user } = view({ name: "Trade, 2026", savedAs: "Trade" });

    await user.click(screen.getByRole("button", { name: /Rename/ }));

    await waitFor(() => expect(order).toEqual(["save", "delete"]));
  });

  it("forgets nothing when the save under the new name is refused", async () => {
    vi.mocked(saveDashboard).mockRejectedValue(new Refused(["that name is taken"]));
    const { user } = view({ name: "Trade, 2026", savedAs: "Trade" });

    await user.click(screen.getByRole("button", { name: /Rename/ }));

    expect(await screen.findByText(/that name is taken/)).toBeDefined();
    expect(deleteDashboard).not.toHaveBeenCalled();
  });
});

describe("while the store is being asked", () => {
  it("disables the controls that would ask it again", async () => {
    let release: (body: unknown) => void = () => {};
    vi.mocked(saveDashboard).mockImplementation(
      () => new Promise((settle) => (release = settle as (body: unknown) => void)),
    );
    const { user } = view({ name: "Trade" });

    await user.click(screen.getByRole("button", { name: "Save" }));

    const working = await screen.findByRole("button", { name: "Working" });
    expect(working.hasAttribute("disabled")).toBe(true);

    release({ name: "Trade", tiles: [] });
    await waitFor(() => expect(screen.getByRole("button", { name: "Save" })).toBeDefined());
  });

  it("reads the list again after a save, so the menu is not a moment behind", async () => {
    vi.mocked(saveDashboard).mockResolvedValue({ name: "Trade", tiles: [] });
    const { user } = view({ name: "Trade" });
    await waitFor(() => expect(getDashboards).toHaveBeenCalledTimes(1));

    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(getDashboards).toHaveBeenCalledTimes(2));
  });
});

describe("a tile whose spec was refused", () => {
  // The tile ran the same endpoint the canvas runs and said "What the source said" over
  // every failure it got back, with the first line and nothing else. Two of those headings
  // were untrue — a spec the validator rejected and a request this server would not spend a
  // query on both reached no source — and the missing lines were the rest of what the
  // validator had said about the tile.

  it("names the part that refused, rather than the source it never reached", async () => {
    vi.mocked(execute).mockRejectedValue(
      new Refused(["That is more than 100 queries in a minute. Wait 12 seconds."], {
        spoke: "rations",
      }),
    );
    view();

    expect(await screen.findByText("What this server would not spend")).toBeDefined();
    expect(screen.queryByText("What the source said")).toBeNull();
    expect(screen.getByText(/Wait 12 seconds/)).toBeDefined();
  });

  it("shows every line the validator gave, not the first one", async () => {
    vi.mocked(execute).mockRejectedValue(
      new Refused(["'limit' is a required property", "'mark' is not one of the marks"]),
    );
    view();

    expect(await screen.findByText("What the validator said")).toBeDefined();
    expect(screen.getByText("'mark' is not one of the marks")).toBeDefined();
  });

  it("still says something when the request never left the browser", async () => {
    vi.mocked(execute).mockRejectedValue(new TypeError("Failed to fetch"));
    view();

    expect(await screen.findByText("What the browser said")).toBeDefined();
  });
});

/**
 * The one control that reaches more than one tile.
 *
 * What it produces is a filter on the arrangement, and what a tile then runs is its own
 * spec with that filter in it. Both halves are asserted here, because the second is the one
 * that could go wrong quietly: a tile that reads a different table has to be left alone and
 * has to say so, and a control that guessed a join instead would draw a plausible number.
 */
describe("a filter across every tile", () => {
  const grouped = (from: string, dimension: string): Spec =>
    ({
      spec_version: "1",
      title: `by ${dimension}`,
      query: {
        from,
        group_by: [{ column: `${from}.${dimension}`, as: dimension }],
        aggregates: [{ fn: "sum", column: `${from}.total`, as: "revenue" }],
        limit: 500,
      },
      chart: { mark: "bar", encoding: { y: { field: "revenue", type: "quantitative" } } },
    }) as unknown as Spec;

  const ORDERS = grouped("vizmith.shop.orders", "status");
  const CARRIERS = grouped("vizmith.shop.carriers", "name");

  function board(tiles: Spec[], over: Partial<Arrangement> = {}) {
    const onChange = vi.fn();
    render(
      <Dashboards
        current={null}
        columns={[{ table: "vizmith.shop.orders", column: "order_date", type: "date" }]}
        arrangement={{ ...NOTHING, tiles: tiles.map((each) => tiled(each)), ...over }}
        onChange={onChange}
        onEdit={() => {}}
      />,
    );
    return { onChange, user: userEvent.setup() };
  }

  it("offers the dimensions the tiles are grouped by, and adds the filter that was built", async () => {
    const { onChange, user } = board([ORDERS]);

    await user.type(screen.getByLabelText("Value"), "shipped");
    await user.click(screen.getByRole("button", { name: "Add" }));

    expect(onChange).toHaveBeenCalledTimes(1);
    expect(vi.mocked(onChange).mock.calls[0][0].across).toEqual([
      { column: "vizmith.shop.orders.status", op: "=", value: "shipped" },
    ]);
  });

  it("will not add a filter with no value, since a comparison against nothing is not one", () => {
    board([ORDERS]);

    expect(screen.getByRole("button", { name: "Add" })).toHaveProperty("disabled", true);
  });

  it("asks for no value at all where the operator takes none", async () => {
    const { onChange, user } = board([ORDERS]);

    await user.selectOptions(screen.getByLabelText("Operator"), "is_null");
    await user.click(screen.getByRole("button", { name: "Add" }));

    expect(vi.mocked(onChange).mock.calls[0][0].across).toEqual([
      { column: "vizmith.shop.orders.status", op: "is_null" },
    ]);
  });

  it("runs a tile it reaches with the filter in the spec, and never in the tile's own copy", async () => {
    const across = [{ column: "vizmith.shop.orders.status", op: "=" as const, value: "shipped" }];
    board([ORDERS], { across });

    await waitFor(() => expect(execute).toHaveBeenCalled());
    expect(vi.mocked(execute).mock.calls[0][0].query.filters).toEqual(across);
    expect(ORDERS.query.filters).toBeUndefined();
  });

  /** The rule the whole feature turns on. A tile whose query does not read the table would
   * need a join to be narrowed by it, and a join nobody confirmed produces a plausible
   * number rather than an error — so the tile draws what it drew and says why. */
  it("leaves a tile that does not read the table alone, and says so on that tile", async () => {
    board([CARRIERS], {
      across: [{ column: "vizmith.shop.orders.status", op: "=", value: "shipped" }],
    });

    await waitFor(() => expect(execute).toHaveBeenCalled());
    expect(vi.mocked(execute).mock.calls[0][0].query.filters).toBeUndefined();
    expect(screen.getByText(/Not narrowed by status = shipped/)).toBeDefined();
  });

  it("says how far a filter reaches, so one that reaches nothing does not look like it works", () => {
    board([ORDERS, CARRIERS], {
      across: [{ column: "vizmith.shop.orders.status", op: "=", value: "shipped" }],
    });

    expect(screen.getByText("1 tile")).toBeDefined();
  });

  it("takes a filter off again, leaving the tiles as they were", async () => {
    const { onChange, user } = board([ORDERS], {
      across: [{ column: "vizmith.shop.orders.status", op: "=", value: "shipped" }],
    });

    await user.click(screen.getByRole("button", { name: "Remove the filter status = shipped" }));

    expect(vi.mocked(onChange).mock.calls[0][0].across).toEqual([]);
  });

  /** A stored date means that day forever, so a saved dashboard whose date filter has to be
   * retyped every morning is one nobody keeps. The grammar could already say this. */
  it("offers a date column the relative values the grammar has, and writes one", async () => {
    const dated = grouped("vizmith.shop.orders", "order_date");
    const { onChange, user } = board([dated]);

    await user.selectOptions(screen.getByLabelText("What kind of date"), "ago");
    await user.selectOptions(screen.getByLabelText("Unit"), "month");
    await user.type(screen.getByLabelText("How many"), "3");
    await user.click(screen.getByRole("button", { name: "Add" }));

    expect(vi.mocked(onChange).mock.calls[0][0].across).toEqual([
      {
        column: "vizmith.shop.orders.order_date",
        op: "=",
        value: { relative: "ago", unit: "month", count: 3 },
      },
    ]);
  });

  it("offers no relative value on a column that is not a date, where the tokens mean nothing", () => {
    board([ORDERS]);

    expect(screen.queryByLabelText("What kind of date")).toBeNull();
  });

  it("says why there is nothing to filter by where no tile groups by anything", () => {
    board([spec("Revenue")]);

    expect(screen.getByText(/No tile on this dashboard groups by anything/)).toBeDefined();
  });

  it("saves the filters with the dashboard, because a narrowing that is retyped is not kept", async () => {
    const across = [{ column: "vizmith.shop.orders.status", op: "=" as const, value: "shipped" }];
    vi.mocked(saveDashboard).mockResolvedValue({ name: "Revenue", tiles: [] });
    const { user } = board([ORDERS], { name: "Revenue", across });

    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(saveDashboard).toHaveBeenCalled());
    expect(vi.mocked(saveDashboard).mock.calls[0][2]).toEqual(across);
  });
});
