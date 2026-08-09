import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import Wells from "./Wells";
import { place, type Draft, type Field } from "../spec/spec";

/**
 * The drop, which is the interaction the wells exist for and the one nothing could reach.
 *
 * Every other test of this file renders it to a string and asserts on the markup, which is
 * the right tool for "what does this draw" and cannot press anything. `drop` asks the
 * resolver for a join path, refuses under the well when there is none, and rewrites the
 * spec when there is — three behaviours whose only previous home was a Playwright run in
 * the other language.
 *
 * `api.ts` is stubbed rather than the network. What is under test is what this component
 * does with an answer; whether the resolver gives the right one is the server's business
 * and has its own tests.
 */
vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return { ...actual, getJoinPath: vi.fn() };
});

const { Refused, getJoinPath } = await import("../api");

const COUNTRY: Field = { table: "vizmith.shop.customers", column: "country", type: "string" };
const TOTAL: Field = { table: "vizmith.shop.orders", column: "total", type: "decimal" };
const CATEGORY: Field = { table: "vizmith.shop.products", column: "category", type: "string" };

/** A chart built by dragging: a dimension off customers and a measure off orders. */
const revenue = (): Draft => place(place(null, "Axis", COUNTRY), "Values", TOTAL);

/**
 * The panel, and a drop into one of its wells.
 *
 * The zone is found inside the well that names it rather than by position, because a well
 * only draws one while it is empty — counting them means counting a number that changes
 * with the spec. It is a button now, so the tests below this one find it by its name; a
 * drop is still fired as a drop, because that is the gesture being driven.
 */
function wells(draft: Draft | null, dragging: Field | null) {
  const onChange = vi.fn();
  const onDrag = vi.fn();
  const onRelationships = vi.fn();
  const { container } = render(
    <Wells
      draft={draft}
      dragging={dragging}
      onChange={onChange}
      onDrag={onDrag}
      onRelationships={onRelationships}
    />,
  );

  const drop = (well: string) => {
    const named = [...container.querySelectorAll(".well")].find((each) =>
      each.querySelector(".well__name")?.textContent?.startsWith(well),
    );
    const zone = named?.querySelector(".well__drop");
    if (!zone) throw new Error(`the ${well} well is not offering a drop zone`);
    fireEvent.dragOver(zone);
    fireEvent.drop(zone);
  };

  return { drop, onChange, onDrag, onRelationships };
}

afterEach(() => vi.mocked(getJoinPath).mockReset());

describe("a column dropped into a well", () => {
  it("rewrites the spec when the column is on a table the query already reads", async () => {
    const { drop, onChange } = wells(null, COUNTRY);

    drop("Axis");

    await waitFor(() => expect(onChange).toHaveBeenCalled());
    const next = onChange.mock.calls[0][0] as Draft;
    expect(next.query.from).toBe("vizmith.shop.customers");
    expect(next.chart.encoding.x?.field).toBe("country");
    expect(getJoinPath).not.toHaveBeenCalled();
  });

  it("asks the resolver for a path when the column is on a table the query does not read", async () => {
    vi.mocked(getJoinPath).mockResolvedValue({
      joins: [
        {
          table: "vizmith.shop.products",
          on: [{ left: "vizmith.shop.orders.product_id", right: "vizmith.shop.products.id" }],
        },
      ],
    });
    const { drop, onChange } = wells(revenue(), CATEGORY);

    drop("Legend");

    await waitFor(() => expect(onChange).toHaveBeenCalled());
    expect(getJoinPath).toHaveBeenCalledWith("vizmith.shop.customers", "vizmith.shop.products");
    expect((onChange.mock.calls[0][0] as Draft).query.joins).toHaveLength(1);
  });

  it("refuses under the well when nothing confirms a path, in the server's own words", async () => {
    // The failure the whole design exists to prevent: a wrong join produces a plausible
    // number rather than an error, so a pair with no confirmed relationship is refused
    // rather than guessed at, and the refusal is the server's sentence and not one here.
    vi.mocked(getJoinPath).mockRejectedValue(
      new Refused([
        "no confirmed relationship joins vizmith.shop.customers to vizmith.shop.products",
      ]),
    );
    const { drop, onChange } = wells(revenue(), CATEGORY);

    drop("Legend");

    expect(await screen.findByText(/no confirmed relationship/)).toBeDefined();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("offers the way to the screen where a relationship is confirmed", async () => {
    vi.mocked(getJoinPath).mockRejectedValue(new Refused(["no confirmed relationship"]));
    const { drop, onRelationships } = wells(revenue(), CATEGORY);

    drop("Legend");
    await userEvent.click(await screen.findByRole("button", { name: "Confirm a relationship" }));

    expect(onRelationships).toHaveBeenCalled();
  });

  it("announces the refusal, since a drop moves focus nowhere", async () => {
    vi.mocked(getJoinPath).mockRejectedValue(new Refused(["no confirmed relationship"]));
    const { drop } = wells(revenue(), CATEGORY);

    drop("Legend");

    expect(await screen.findByRole("alert")).toBeDefined();
  });

  it("clears the previous refusal when the next drop is asked for", async () => {
    vi.mocked(getJoinPath).mockRejectedValue(new Refused(["no confirmed relationship"]));
    const { drop } = wells(revenue(), CATEGORY);
    drop("Legend");
    await screen.findByRole("alert");

    vi.mocked(getJoinPath).mockResolvedValue({ joins: [] });
    drop("Legend");

    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  });

  it("does nothing at all when nothing is being dragged", async () => {
    const { drop, onChange } = wells(revenue(), null);

    drop("Filters");

    await waitFor(() => expect(getJoinPath).not.toHaveBeenCalled());
    expect(onChange).not.toHaveBeenCalled();
  });
});

/**
 * The same result, without a mouse.
 *
 * Dragging a column into a well is the primary interaction of the product and it was
 * implemented entirely in HTML5 drag and drop, so a keyboard only person could read the
 * Fields panel, reach every control around it, and not build a chart at all — WCAG 2.1.1,
 * level A. What closes it is an input path and not a second feature: picking a field up
 * sets the same state a drag sets, and the well presses the same `drop`.
 */
describe("a field placed without a mouse", () => {
  it("places what is held in the well that was pressed", async () => {
    const user = userEvent.setup();
    const { onChange } = wells(null, COUNTRY);

    await user.click(screen.getByRole("button", { name: "Place country in Axis" }));

    await waitFor(() => expect(onChange).toHaveBeenCalled());
    const next = onChange.mock.calls[0][0] as Draft;
    expect(next.chart.encoding.x?.field).toBe("country");
  });

  it("says what pressing a well will do with what is held", async () => {
    // "Drop a field here" describes a gesture the keyboard does not have. The visible text
    // is inside the accessible name rather than replaced by it, so somebody driving this
    // by voice can say what they can see.
    wells(revenue(), CATEGORY);

    expect(screen.getByRole("button", { name: "Place category in Legend" })).toBeTruthy();
    // Every empty well says it, because any of them is where it might go.
    expect(screen.getAllByText("Place category").length).toBeGreaterThan(1);
  });

  it("puts the field down once it has landed, so the next Return does not place it twice", async () => {
    const user = userEvent.setup();
    const { onDrag } = wells(null, COUNTRY);

    await user.click(screen.getByRole("button", { name: "Place country in Axis" }));

    await waitFor(() => expect(onDrag).toHaveBeenCalledWith(null));
  });

  it("keeps holding a field the well refused, since the next thing is another well", async () => {
    const user = userEvent.setup();
    // Top N ranks a column the chart already groups by, and this one is not one.
    const { onDrag, onChange } = wells(revenue(), CATEGORY);

    await user.click(screen.getByRole("button", { name: "Place category in Top N" }));

    await screen.findByRole("alert");
    expect(onChange).not.toHaveBeenCalled();
    expect(onDrag).not.toHaveBeenCalled();
  });

  it("offers a way out, because a field picked up is a mode somebody can be stuck in", async () => {
    const user = userEvent.setup();
    const { onDrag } = wells(revenue(), CATEGORY);

    await user.click(screen.getByRole("button", { name: "Place category in Legend" }));
    await user.keyboard("{Escape}");

    expect(onDrag).toHaveBeenCalledWith(null);
  });

  it("says what is held, for somebody who cannot see a row go into its pressed state", () => {
    const { container } = render(
      <Wells
        draft={null}
        dragging={COUNTRY}
        onChange={() => {}}
        onDrag={() => {}}
        onRelationships={() => {}}
      />,
    );

    expect(container.querySelector('[role="status"]')?.textContent).toContain("Holding country");
  });
});

/**
 * What the Values well says the measure is.
 *
 * The select is the aggregate's, and a window is not an aggregate: it is taken over one.
 * The well used to show `sum` for a chart drawing a running total, because no aggregate
 * carried the drawn column's alias and the lookup fell back — a well naming an inference
 * nobody made, which is the thing this panel exists to prevent.
 */
describe("the measure the Values well names", () => {
  const monthly = (): Draft => ({
    spec_version: "1",
    query: {
      from: "orders",
      group_by: [{ column: "orders.order_date", truncate: "month", as: "month" }],
      aggregates: [{ fn: "sum", column: "orders.total", as: "revenue" }],
      windows: [{ fn: "running_total", of: "revenue", along: "month", as: "revenue_so_far" }],
      limit: 60,
    },
    chart: {
      mark: "area",
      encoding: {
        x: { field: "month", type: "temporal" },
        y: { field: "revenue_so_far", type: "quantitative" },
      },
    },
  });

  it("is a control where the measure is an aggregate", async () => {
    const user = userEvent.setup();
    const { onChange } = wells(revenue(), null);

    await user.selectOptions(screen.getByLabelText("Aggregate"), "avg");

    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({
        query: expect.objectContaining({ aggregates: [expect.objectContaining({ fn: "avg" })] }),
      }),
    );
  });

  it("is the window and its measure where the chart draws a window", () => {
    const { container } = render(
      <Wells
        draft={monthly()}
        dragging={null}
        onChange={() => {}}
        onDrag={() => {}}
        onRelationships={() => {}}
      />,
    );

    expect(screen.queryByLabelText("Aggregate")).toBeNull();
    expect(container.textContent).toContain("running_total of revenue");
    expect(container.textContent).toContain("revenue_so_far");
  });

  it("still offers the way back out, because a drop with no way back is a trap", async () => {
    const user = userEvent.setup();
    const { onChange } = wells(monthly(), null);

    await user.click(screen.getByRole("button", { name: "Remove from Values" }));

    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ query: expect.not.objectContaining({ windows: expect.anything() }) }),
    );
  });
});
