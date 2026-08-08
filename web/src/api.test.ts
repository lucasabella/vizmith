import { afterEach, describe, expect, it, vi } from "vitest";
import { ask, execute, getHealth, Refused } from "./api";
import type { Spec } from "./chart/option";

/**
 * The transport, on its own.
 *
 * Everything above this file — the canvas, a dashboard tile, the panels — reads a failure
 * through `refusal()` in `outcome.ts`, and everything it reads comes off the `Refused`
 * thrown here. So what is under test is the one translation that decides all of them: an
 * HTTP response, turned into the words the server used and the name it gave to whichever
 * part refused. Get `said` or `spoke` wrong here and every caller shows the wrong heading.
 *
 * These are the four bodies a server actually returns, not four hypotheticals: a rejected
 * spec, a part that named itself, a failure with no body at all, and an answer.
 */

const responding = (status: number, body: unknown) =>
  vi.fn(() =>
    Promise.resolve({
      ok: status < 400,
      status,
      statusText: status === 500 ? "Internal Server Error" : "Bad Request",
      json: () => (body === undefined ? Promise.reject(new Error("no body")) : Promise.resolve(body)),
    } as Response),
  );

const spec = { chart: { mark: "bar", encoding: {} } } as unknown as Spec;

afterEach(() => vi.unstubAllGlobals());

async function refused(status: number, body: unknown): Promise<Refused> {
  vi.stubGlobal("fetch", responding(status, body));
  try {
    await execute(spec);
  } catch (error) {
    return error as Refused;
  }
  throw new Error("the request was not refused");
}

describe("what a refusal carries off the wire", () => {
  it("keeps the server's own list rather than a message written here", async () => {
    const error = await refused(400, {
      errors: ["'limit' is a required property", "'mark' is not one of the marks"],
    });

    expect(error).toBeInstanceOf(Refused);
    expect(error.errors).toHaveLength(2);
    expect(error.said).toBe(true);
    expect(error.spoke).toBeUndefined();
  });

  it("carries the name the server gave to the part that refused", async () => {
    // The field exists because from the browser a question is one request, and the source,
    // the model, the spec check and this server's own rationing are four different things
    // to tell somebody about.
    const error = await refused(429, {
      errors: ["That is more than 20 model requests in a minute."],
      spoke: "rations",
    });

    expect(error.spoke).toBe("rations");
  });

  it("separates a server that failed from a validator with nothing to say", async () => {
    // No body, or a body that is not the shape this API answers in. There is no list, so
    // `said` is false and the status line is all there is — which is a different thing to
    // show than a validator that rejected a spec.
    const error = await refused(500, undefined);

    expect(error.said).toBe(false);
    expect(error.errors).toEqual(["500 Internal Server Error"]);
  });

  it("says something as a plain Error too, for whatever only reads a message", async () => {
    const error = await refused(400, { errors: ["'limit' is a required property"] });

    expect(error.message).toBe("'limit' is a required property");
    expect(new Refused([]).message).toBe("the server refused the request");
  });

  it("carries what the attempt cost, because the expensive case is the one that failed", async () => {
    const error = await refused(400, {
      errors: ["no spec survived three attempts"],
      spoke: "model",
      cost: { calls: 3, prompt: 4200, completion: 260, total: 4460 },
    });

    expect(error.cost?.calls).toBe(3);
    expect(error.cost?.total).toBe(4460);
  });
});

describe("the requests themselves", () => {
  it("asks a question as a question, on the endpoint that answers one", async () => {
    // `App.tsx` held this `fetch` and its own reading of what came back, which is how the
    // canvas and a tile came to disagree about a refusal in the first place.
    const fetching = responding(200, { spec, rows: [], cost: { calls: 1 } });
    vi.stubGlobal("fetch", fetching);

    await ask("revenue by country");

    const [url, options] = fetching.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/ask");
    expect(options.method).toBe("POST");
    expect(JSON.parse(String(options.body))).toEqual({ question: "revenue by country" });
  });

  it("reads what the server says about itself, which is what the controls gate on", async () => {
    vi.stubGlobal(
      "fetch",
      responding(200, { status: "ok", version: "0.1.0", source: true, model: false }),
    );

    const health = await getHealth();

    expect(health.source).toBe(true);
    expect(health.model).toBe(false);
  });
});
