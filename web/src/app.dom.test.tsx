import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

/**
 * The application, driven far enough to reach the things that only exist between two
 * interactions.
 *
 * `runs.ts` argues that a superseded answer must be dropped rather than drawn, because a
 * warehouse does not answer in the order it was asked and what a late answer leaves on
 * screen is the new spec in the editor beside the chart drawn from the old one.
 * `runs.test.ts` proves the counter works. Nothing proved the counter was *used*, and a
 * counter nobody consults is the same as no counter. That is what this file is for.
 *
 * Two runs overlap through a drop rather than through the editor, because that is the only
 * way it happens: `Run spec` and the question field are both disabled while a run is in
 * flight, and the wells are not. So the second run here is a column dragged into a well
 * while the first is still being waited for, which is the gesture a person actually makes.
 */

const HEALTH = { version: "0.0.1", source: true, model: true };

const SHAPE = {
  tables: [
    {
      table: "vizmith.shop.orders",
      columns: [
        { name: "total", type: "decimal" },
        { name: "status", type: "string" },
      ],
    },
  ],
};

const TYPED = {
  spec_version: "1",
  title: "typed",
  query: {
    from: "vizmith.shop.orders",
    aggregates: [{ fn: "sum", column: "vizmith.shop.orders.total", as: "revenue" }],
    limit: 500,
  },
  chart: { mark: "bar", encoding: { y: { field: "revenue", type: "quantitative" } } },
};

/** Every request the interface makes, answered — except `/api/execute`, which is held so a
 * test decides the order the answers come back in. */
function serving() {
  const waiting: ((answer: object[] | { rows: object[]; cost?: object }) => void)[] = [];

  const fetching = vi.fn((url: string, options?: RequestInit) => {
    const path = String(url);
    if (path.endsWith("/api/health")) return answered(HEALTH);
    if (path.endsWith("/api/shape")) return answered(SHAPE);
    if (path.endsWith("/api/tables")) return answered({ tables: [] });
    if (path.endsWith("/api/execute")) {
      const sent = JSON.parse(String(options?.body ?? "{}"));
      return new Promise((settle) => {
        waiting.push((answer) =>
          settle({
            ok: true,
            // An answer is rows, and may carry what the question cost. A test that only
            // cares about the rows passes an array, which is what most of them do.
            json: () =>
              Promise.resolve(
                Array.isArray(answer)
                  ? { spec: sent.spec, rows: answer }
                  : { spec: sent.spec, ...answer },
              ),
          } as Response),
        );
      });
    }
    return answered({});
  });

  return { fetching, waiting };
}

const answered = (body: unknown) =>
  Promise.resolve({ ok: true, json: () => Promise.resolve(body) } as Response);

let waiting: ((answer: object[] | { rows: object[]; cost?: object }) => void)[];

beforeEach(() => {
  const serve = serving();
  waiting = serve.waiting;
  vi.stubGlobal("fetch", serve.fetching);
});

afterEach(() => vi.unstubAllGlobals());

const rows = (many: number) => Array.from({ length: many }, (_, at) => ({ revenue: at }));

/** Paste a spec into `{ } JSON` and press Run, which is the one way into a chart that needs
 * neither a model nor a drag. Leaves the editor open. */
async function typeAndRun() {
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "{ } JSON" }));
  const editor = screen.getByLabelText("Chart specification, as JSON");
  await user.clear(editor);
  await user.paste(JSON.stringify(TYPED));
  await user.click(screen.getByRole("button", { name: "Run spec" }));
}

/** A column out of the Fields tree and into a well, which is the second run: the wells stay
 * live while one is in flight, which is exactly how two of them overlap. */
async function dragIntoAWell(container: HTMLElement) {
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "{ } JSON" }));
  await user.click(screen.getByRole("button", { name: /orders/ }));

  // By the name element rather than the row's text, which begins with the drag grip.
  const column = [...container.querySelectorAll(".tree__row--column")].find(
    (row) => row.querySelector(".tree__name")?.textContent === "status",
  );
  if (!column) throw new Error("the Fields tree is not offering a column to drag");
  fireEvent.dragStart(column);

  const axis = [...container.querySelectorAll(".well")].find((each) =>
    each.querySelector(".well__name")?.textContent?.startsWith("Axis"),
  );
  const zone = axis?.querySelector(".well__drop");
  fireEvent.dragOver(zone!);
  fireEvent.drop(zone!);
}

async function started() {
  render(<App />);
  await screen.findByRole("button", { name: "{ } JSON" });
  await screen.findByRole("button", { name: /orders/ });
}

describe("two answers arriving out of order", () => {
  it("draws the run still being waited for and drops the one it superseded", async () => {
    const { container } = render(<App />);
    await screen.findByRole("button", { name: /orders/ });

    await typeAndRun();
    await waitFor(() => expect(waiting).toHaveLength(1));
    await dragIntoAWell(container);
    await waitFor(() => expect(waiting).toHaveLength(2));

    // The second answers first, which is the ordinary case and has to draw.
    waiting[1](rows(2));
    await waitFor(() => expect(screen.getByText("2 rows")).toBeDefined());

    // The first answers last. It is the superseded one, and it must change nothing: the
    // alternative is the new spec in the editor beside a chart drawn from the old answer.
    waiting[0](rows(9));
    await new Promise((settle) => setTimeout(settle, 30));

    expect(screen.getByText("2 rows")).toBeDefined();
    expect(screen.queryByText("9 rows")).toBeNull();
  });

  it("stays on the wait when a superseded answer arrives first", async () => {
    // The other half of the same rule. A late answer must not take the canvas off "Running
    // the spec" while the run being waited for is still in flight.
    const { container } = render(<App />);
    await screen.findByRole("button", { name: /orders/ });

    await typeAndRun();
    await waitFor(() => expect(waiting).toHaveLength(1));
    await dragIntoAWell(container);
    await waitFor(() => expect(waiting).toHaveLength(2));

    waiting[0](rows(9));
    await new Promise((settle) => setTimeout(settle, 30));

    expect(screen.getByText("Running the spec")).toBeDefined();
    expect(screen.queryByText("9 rows")).toBeNull();
  });
});

describe("what the canvas announces", () => {
  it("says what is in flight, and then what landed", async () => {
    await started();
    // The region that announces the canvas, which is the hidden one. The visual card has
    // a second status region for what an export press just did — a different statement,
    // and the reason this query names which region it means.
    const said = () => document.querySelector(".visually-hidden[role='status']")?.textContent;

    await typeAndRun();
    await waitFor(() => expect(said()).toBe("Running the spec."));

    waiting[0](rows(1));
    await waitFor(() => expect(said()).toBe("One figure."));
  });
});

describe("what a question cost", () => {
  it("is shown beside the row count, with the attempts named", async () => {
    // The claim the project is built on is that a profile rather than rows keeps token cost
    // bounded, and the number that shows it was measured on every request and thrown away.
    // Three attempts is the case worth naming: it cost three times what one attempt does.
    await started();
    await typeAndRun();
    waiting[0]({
      rows: rows(1),
      cost: { calls: 3, prompt: 12000, completion: 600, total: 12600 },
    });

    expect(await screen.findByText(/12,600 tokens on this question, over 3 attempts/)).toBeDefined();
  });

  it("goes away when the next answer reached no model", async () => {
    // Running a spec by hand reaches no model, so a cost left under it would be a figure
    // about a question the chart on screen is not the answer to.
    await started();
    await typeAndRun();
    waiting[0]({ rows: rows(1), cost: { calls: 1, prompt: 4000, completion: 200, total: 4200 } });
    await screen.findByText(/4,200 tokens/);

    // The same spec again, through the editor that is already open: `typeAndRun` toggles
    // the panel, so pressing Run is the second run rather than a second visit.
    await userEvent.setup().click(screen.getByRole("button", { name: "Run spec" }));
    await waitFor(() => expect(waiting).toHaveLength(2));
    waiting[1]({ rows: rows(1) });

    await waitFor(() => expect(screen.queryByText(/tokens/)).toBeNull());
  });
});
