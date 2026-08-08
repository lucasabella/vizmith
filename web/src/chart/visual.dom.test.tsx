import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import Visual from "./Visual";
import type { Row, Spec } from "./option";
import type { Draft, Field } from "../spec/spec";

/**
 * The drill: a mark clicked, a dimension chosen, and the spec that produces.
 *
 * The renderer is stubbed for one reason and it is not convenience. A drill begins with a
 * click on an ECharts canvas, and a canvas is the one thing jsdom genuinely cannot give —
 * so the choice is between stubbing the renderer here or leaving every line of `narrow`
 * unreachable outside a browser. `Chart.tsx` has its own coverage and the browser suite
 * drives a real mark click; what is missing, and what this is, is everything that happens
 * after `onSelect` fires.
 */
vi.mock("./Deferred", () => ({
  default: ({ onSelect }: { onSelect?: (clicked: unknown) => void }) => (
    <button onClick={() => onSelect?.({ category: "Netherlands", series: undefined })}>
      a mark
    </button>
  ),
}));

const SPEC = {
  spec_version: "1",
  title: "Revenue by country",
  query: {
    from: "vizmith.shop.orders",
    joins: [
      {
        table: "vizmith.shop.customers",
        on: [{ left: "vizmith.shop.orders.customer_id", right: "vizmith.shop.customers.id" }],
      },
    ],
    group_by: [{ column: "vizmith.shop.customers.country", as: "country" }],
    aggregates: [{ fn: "sum", column: "vizmith.shop.orders.total", as: "revenue" }],
    limit: 500,
  },
  chart: {
    mark: "bar",
    encoding: {
      x: { field: "country", type: "nominal" },
      y: { field: "revenue", type: "quantitative" },
    },
  },
} as unknown as Spec;

const ROWS: Row[] = [{ country: "Netherlands", revenue: 91240 }];

const COLUMNS: Field[] = [
  { table: "vizmith.shop.orders", column: "status", type: "string" },
  { table: "vizmith.shop.customers", column: "country", type: "string" },
  { table: "vizmith.shop.products", column: "category", type: "string" },
];

function visual() {
  const onDrill = vi.fn();
  render(<Visual spec={SPEC} rows={ROWS} columns={COLUMNS} onDrill={onDrill} />);
  return { onDrill, user: userEvent.setup() };
}

describe("clicking a mark", () => {
  it("asks what to group the narrowed question by", async () => {
    // The one decision a click cannot make on its own, so it is asked rather than guessed.
    const { user } = visual();

    await user.click(screen.getByRole("button", { name: "a mark" }));

    expect(await screen.findByText(/Ask about Netherlands/)).toBeDefined();
  });

  it("offers only the columns the query already reads, because a join a click made is one nobody confirmed", async () => {
    const { user } = visual();

    await user.click(screen.getByRole("button", { name: "a mark" }));
    await screen.findByText(/Ask about/);

    expect(screen.getByText("status")).toBeDefined();
    // On products, which this query does not read. Reaching it would need a join, and a
    // join a click made is a join nobody confirmed.
    expect(screen.queryByText("category")).toBeNull();
    // Already the axis, so grouping by it again asks the question that is on screen.
    expect(screen.queryByText("country")).toBeNull();
  });

  it("narrows the spec to what was clicked, grouped by the dimension chosen", async () => {
    const { onDrill, user } = visual();

    await user.click(screen.getByRole("button", { name: "a mark" }));
    await screen.findByText(/Ask about/);
    await user.click(screen.getByRole("button", { name: /status/ }));

    await waitFor(() => expect(onDrill).toHaveBeenCalled());
    const next = onDrill.mock.calls[0][0] as Draft;
    const filters = next.query.filters ?? [];
    expect(filters.some((filter) => filter.value === "Netherlands")).toBe(true);
    expect(next.chart.encoding.x?.field).toBe("status");
  });

  it("closes without asking anything when the way out is taken", async () => {
    const { onDrill, user } = visual();

    await user.click(screen.getByRole("button", { name: "a mark" }));
    await screen.findByText(/Ask about/);
    await user.click(screen.getByRole("button", { name: "Never mind" }));

    await waitFor(() => expect(screen.queryByText(/Ask about/)).toBeNull());
    expect(onDrill).not.toHaveBeenCalled();
  });

  it("shows the table of what the chart was drawn from, which is what makes the colours legal", async () => {
    const { user } = visual();

    await user.click(screen.getByRole("button", { name: "Table" }));

    expect(await screen.findByText("Netherlands")).toBeDefined();
  });
});
