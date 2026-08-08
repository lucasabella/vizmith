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
 * with the spec. There is no role to query by, which is the keyboard gap #143 is about.
 */
function wells(draft: Draft | null, dragging: Field | null) {
  const onChange = vi.fn();
  const onRelationships = vi.fn();
  const { container } = render(
    <Wells draft={draft} dragging={dragging} onChange={onChange} onRelationships={onRelationships} />,
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

  return { drop, onChange, onRelationships };
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
