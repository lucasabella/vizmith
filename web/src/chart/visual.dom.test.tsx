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

/**
 * The three ways out. The pure half — the escaping, the file name — is `exporting.test.ts`;
 * this is the half that only exists once a control has been pressed, which is what the
 * control does with what that produces.
 *
 * The stub above draws no canvas and therefore announces no instance, which is also the
 * state a real card is in while the renderer is being fetched. That makes it exactly the
 * right double for one of these: a control that saves a picture must be disabled when
 * there is no picture.
 */
/** jsdom defines `navigator.clipboard` with a getter and no setter, so it is redefined
 * rather than assigned. Restored by nothing, because each of these installs its own. */
const clipboard = (writeText: (text: string) => Promise<void>) =>
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });

describe("getting a chart out of the tab", () => {
  it("puts the spec on the clipboard, and says so", async () => {
    // `userEvent.setup()` installs its own clipboard, which is the closest thing to a real
    // one here, so this reads what the control wrote rather than stubbing the write.
    const { user } = visual();

    await user.click(screen.getByRole("button", { name: "Copy the spec" }));

    expect(JSON.parse(await navigator.clipboard.readText())).toEqual(SPEC);
    expect(await screen.findByText("Spec copied.")).toBeDefined();
  });

  it("says it could not, where the browser refuses the clipboard", async () => {
    // Absent on an insecure origin and refusable by permission. A control that says
    // "Copied" when nothing was copied is worse than one that says it could not.
    const { user } = visual();
    // After `visual`, because `userEvent.setup` installs a clipboard of its own and would
    // otherwise put a working one back over this.
    clipboard(() => Promise.reject(new Error("denied")));

    await user.click(screen.getByRole("button", { name: "Copy the spec" }));

    expect(await screen.findByText(/would not let this page write to the clipboard/)).toBeDefined();
  });

  it("hands the rows over as a file named after the chart", async () => {
    const saved: { name: string; type: string }[] = [];
    const url = vi.fn(() => "blob:rows");
    Object.assign(URL, { createObjectURL: url, revokeObjectURL: vi.fn() });
    const clicking = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(function (this: HTMLAnchorElement) {
        saved.push({ name: this.download, type: this.href });
      });
    const { user } = visual();

    await user.click(screen.getByRole("button", { name: "Rows as CSV" }));

    expect(saved).toEqual([{ name: "revenue-by-country.csv", type: "blob:rows" }]);
    expect(await screen.findByText("Saved revenue-by-country.csv.")).toBeDefined();
    clicking.mockRestore();
  });

  it("will not offer a picture of a chart nothing has drawn", async () => {
    visual();

    expect(screen.getByRole("button", { name: "Chart as PNG" })).toHaveProperty("disabled", true);
  });
});
