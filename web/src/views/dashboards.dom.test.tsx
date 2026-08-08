import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import Dashboards from "./Dashboards";
import { NOTHING, tiled, type Arrangement } from "../dashboard/dashboard";
import type { Spec } from "../chart/option";

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

const { Refused, deleteDashboard, getDashboards, saveDashboard } = await import("../api");

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
