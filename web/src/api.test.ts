import { afterEach, describe, expect, it, vi } from "vitest";
import { ask, execute, getHealth, Refused, type Step } from "./api";
import type { Spec } from "./spec/spec";

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
      headers: new Headers({ "content-type": "application/json" }),
      body: null,
      json: () => (body === undefined ? Promise.reject(new Error("no body")) : Promise.resolve(body)),
    } as Response),
  );

/** A response that arrives as an event stream, in the chunks given. The chunks are what the
 * network handed over and not what the server wrote: a frame is split wherever a packet
 * ended, which is the case the reader has to survive and the reason it keeps a buffer. */
const streaming = (...chunks: string[]) =>
  vi.fn(() =>
    Promise.resolve({
      ok: true,
      status: 200,
      statusText: "OK",
      headers: new Headers({ "content-type": "text/event-stream; charset=utf-8" }),
      body: new ReadableStream<Uint8Array>({
        start(controller) {
          const encoder = new TextEncoder();
          for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
          controller.close();
        },
      }),
      json: () => Promise.reject(new Error("this one is a stream")),
    } as unknown as Response),
  );

const event = (name: string, body: unknown) => `event: ${name}\ndata: ${JSON.stringify(body)}\n\n`;

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
    // The stream is asked for by Accept, so the same endpoint answers both ways and a
    // caller that predates it is answered the way it always was.
    expect(String((options.headers as Record<string, string>).Accept)).toContain("text/event-stream");
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

/**
 * The steps, which are the other thing this endpoint answers with.
 *
 * A question reads the profiles, asks the model up to three times and then runs the query,
 * and on a large schema the part in front of the model is the long one. What is under test
 * is the reader: the frames are the server's, split where a network would split them.
 */
describe("a question answered as a stream", () => {
  const answer = { spec, rows: [], cost: { calls: 1, prompt: 10, completion: 2, total: 12 } };

  it("hears each step as it starts, and answers with the last event", async () => {
    vi.stubGlobal(
      "fetch",
      streaming(
        event("step", { step: "profiles", attempt: 0, of: 0 }),
        event("step", { step: "model", attempt: 1, of: 3 }),
        event("step", { step: "query", attempt: 0, of: 0 }),
        event("answer", answer),
      ),
    );
    const heard: Step[] = [];

    const answered = await ask("revenue by country", (step) => heard.push(step));

    expect(heard.map((step) => step.step)).toEqual(["profiles", "model", "query"]);
    expect(heard[1].of).toBe(3);
    expect(answered.cost?.calls).toBe(1);
  });

  it("reads a frame the network split down the middle", async () => {
    // A chunk boundary falls where the packet ended, not where a frame does. Splitting the
    // buffer and keeping the tail is the whole of why this is not one JSON.parse.
    const frames = event("step", { step: "model", attempt: 2, of: 3 }) + event("answer", answer);
    const at = frames.indexOf("attempt") + 3;
    vi.stubGlobal("fetch", streaming(frames.slice(0, at), frames.slice(at)));
    const heard: Step[] = [];

    await ask("revenue by country", (step) => heard.push(step));

    expect(heard).toEqual([{ step: "model", attempt: 2, of: 3 }]);
  });

  it("throws what a refusal event carries, since the status line could not say it", async () => {
    // The headers went out before the first step ran, so the response is a 200 and the name
    // of the event is the only thing that says otherwise.
    vi.stubGlobal(
      "fetch",
      streaming(
        event("step", { step: "profiles", attempt: 0, of: 0 }),
        event("refused", { errors: ["the warehouse is asleep"], spoke: "source" }),
      ),
    );

    const error = await ask("anything").catch((thrown: Refused) => thrown);

    expect(error).toBeInstanceOf(Refused);
    expect((error as Refused).spoke).toBe("source");
    expect((error as Refused).errors).toEqual(["the warehouse is asleep"]);
  });

  it("says so when the stream ends without answering", async () => {
    vi.stubGlobal("fetch", streaming(event("step", { step: "model", attempt: 1, of: 3 })));

    const error = await ask("anything").catch((thrown: Refused) => thrown);

    expect((error as Refused).said).toBe(false);
    expect((error as Refused).errors[0]).toContain("never arrived");
  });

  it("reads a body from a server that answered with one instead", async () => {
    // Rationing and the host check refuse before the endpoint runs, so they answer with a
    // status and a body however the request asked to be answered.
    vi.stubGlobal("fetch", responding(429, { errors: ["36 more seconds"], spoke: "rations" }));

    const error = await ask("anything").catch((thrown: Refused) => thrown);

    expect((error as Refused).spoke).toBe("rations");
  });

  it("ignores an event it has no name for, rather than failing on it", async () => {
    vi.stubGlobal("fetch", streaming(event("heartbeat", {}), event("answer", answer)));

    expect((await ask("revenue by country")).rows).toEqual([]);
  });
});
