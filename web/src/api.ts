/**
 * What the server answers, and the shapes it answers in.
 *
 * Every type here mirrors something Python produced. Nothing in the browser recomputes
 * one: a profile is the profiler's, a relationship is the catalog's, and a join path is
 * the resolver's. The panels show what came back and add no opinion about it.
 */

import type { Row } from "./chart/option";
import type { Spec } from "./spec/spec";
import { asStored, type Dashboard, type Saved, type Tile } from "./dashboard/dashboard";
import type { Across } from "./dashboard/across";
import type { Join } from "./spec/spec";

export type ColumnProfile = {
  name: string;
  type: string;
  null_rate: number;
  distinct_count: number;
  distinct_count_exact: boolean;
  minimum: string | null;
  maximum: string | null;
  samples: string[];
};

export type TableProfile = { table: string; row_count: number; columns: ColumnProfile[] };

/** A table as `describe` alone knows it: a name, and its columns with their types. Not a
 * narrower profile — there is no figure in here that a profile would have, because a figure
 * costs a pass over the table and this endpoint costs none. */
export type TableShape = { table: string; columns: { name: string; type: string }[] };

export type Relationship = {
  left_table: string;
  left_column: string;
  right_table: string;
  right_column: string;
  kind: "declared" | "suggested";
  state: "confirmed" | "rejected" | "open";
};

/**
 * Which part refused, as the server named it. It is the only thing that can: a question
 * passes through the source, the model and the source again, and from the browser they are
 * one request.
 *
 * It lives here rather than beside the sentences it is shown with, because it is the
 * server's own field — `spoke` in `api.py` — and this file is the transcript of what the
 * server answers. What each one *means* to a person is `SAID` in `outcome.ts`, and that is
 * this interface's opinion. The line falls between the field and the sentence.
 */
export type Spoke = "source" | "model" | "spec" | "rations";

/** A refusal, carrying the server's own words. They are what the interface shows: a
 * message written here would be a second account of something that already has one.
 *
 * `spoke` is on the error rather than in a branch of whoever called, so one refusal reads
 * the same wherever it is caught — which it did not: the canvas showed the heading and
 * every line, and a dashboard tile showed the first line of the same refusal with no
 * heading, because the two called the same endpoint two different ways. */
export class Refused extends Error {
  readonly errors: string[];
  readonly spoke?: Spoke;
  /** Whether the words came out of the body. False is a server that failed without saying
   * what failed, which is a different thing to show than a validator's list and used to be
   * a third branch inside `App.tsx`. */
  readonly said: boolean;
  /** What the attempt cost, where the refusal carries one. A question that took three
   * attempts and produced nothing is the expensive case, so the number rides on the
   * failure as well as on the answer. */
  readonly cost?: Cost;

  constructor(errors: string[], carried: { spoke?: Spoke; said?: boolean; cost?: Cost } = {}) {
    super(errors[0] ?? "the server refused the request");
    this.errors = errors;
    this.spoke = carried.spoke;
    this.said = carried.said ?? true;
    this.cost = carried.cost;
  }
}

/** A refusal out of a body the server sent. One reading of it, because a refusal arrives
 * two ways now — as a status with a body, and as an event named `refused` on a stream that
 * could not use a status — and the two have to become the same thing. */
const refusedBy = (body: Refusing, fallback: string): Refused =>
  new Refused(body.errors ?? [fallback], {
    spoke: body.spoke,
    said: Array.isArray(body.errors),
    cost: body.cost,
  });

type Refusing = { errors?: string[]; spoke?: Spoke; cost?: Cost };

async function json<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw refusedBy(body, `${response.status} ${response.statusText}`);
  }
  return body as T;
}

const posted = (payload: object): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(payload),
});

/** What the server says about itself, and what the interface gates on: a question needs a
 * model, and everything else needs a source. Three flags that decide what is enabled were
 * the one part of this API with no declared shape. */
export type Health = { status: string; version: string; source: boolean; model: boolean };

export const getHealth = (): Promise<Health> => json("/api/health");

/** Every table, as the profile the model was given. One request rather than a listing and
 * a fan-out of one request per table: the server built these to answer the listing anyway,
 * and asking for them back paid a second freshness check per table, each of which is a
 * statement the warehouse bills for. */
export const getTables = (): Promise<{ tables: TableProfile[] }> => json("/api/tables");

/** The schema's shape, which is what the panel is drawn from first. It costs no statement,
 * so it answers in the time a metadata read takes rather than in the time profiling a
 * schema takes — which on a schema nobody has profiled is the difference between a table
 * name on screen and 25 seconds of spinner. `getTables` follows and replaces it. */
export const getShape = (): Promise<{ tables: TableShape[] }> => json("/api/shape");

/** One table, for a caller that wants one. The panel is filled by the request above. */
export const getProfile = (name: string): Promise<TableProfile> =>
  json(`/api/tables/${encodeURIComponent(name)}`);

export const getRelationships = (): Promise<{ relationships: Relationship[] }> =>
  json("/api/relationships");

export const answerRelationship = (
  relationship: Relationship,
  answer: "confirmed" | "rejected" | "open",
): Promise<unknown> =>
  json("/api/relationships", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      left_table: relationship.left_table,
      left_column: relationship.left_column,
      right_table: relationship.right_table,
      right_column: relationship.right_column,
      answer,
    }),
  });

/** The rows a spec produces, from the one endpoint that runs one. A tile on a dashboard
 * goes through this exactly as a single chart does, so there is no second answer to what a
 * spec means. */
export const execute = (spec: Spec): Promise<Answered> => json("/api/execute", posted({ spec }));

/** Which part of answering a question is running. The names are the server's — `STEPS` in
 * `ask.py`, held to this list by `mirrors.test.ts` — and what each one says to a person is
 * `STEP` in `outcome.ts`. The same line the `spoke` field falls on. */
export const STEPS = ["profiles", "model", "query"] as const;
export type StepName = (typeof STEPS)[number];

/** A step that has started. `attempt` and `of` are the retry loop's and are zero on a step
 * that has no attempts, which is every step but the model. */
export type Step = { step: StepName; attempt: number; of: number };

const EVENTS = "text/event-stream";

/**
 * A question, answered as the spec it produced and the rows that spec returned, with what
 * asking cost — and, while it is being answered, which step is running.
 *
 * A question is not one wait. It reads the profiles, builds the relationship graph, asks
 * the model up to three times and then runs the query, and on a large schema the metadata
 * in front of the model is the long part. `watching` is called as each step starts, which
 * is what turns one spinner into a sentence that names the slow part.
 *
 * The stream is asked for by `Accept` and the answer is its last event, so what comes back
 * here is what the JSON body always held. A server that answered with a body anyway — an
 * older one, or a refusal decided before the endpoint ran, which is what rationing and the
 * host check are — is read as a body, because a caller waiting for a frame that is never
 * coming is worse than a caller that heard no steps.
 */
export async function ask(question: string, watching?: (step: Step) => void): Promise<Answered> {
  const request = posted({ question });
  const response = await fetch("/api/ask", {
    ...request,
    headers: { ...request.headers, Accept: `${EVENTS}, application/json` },
  });
  const streaming = (response.headers.get("content-type") ?? "").includes(EVENTS);
  if (!streaming || response.body === null) {
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw refusedBy(body, `${response.status} ${response.statusText}`);
    return body as Answered;
  }
  return await heard(response.body, watching);
}

/**
 * The stream, read to its one answer.
 *
 * The status line said 200 before any of this ran, because the headers went out before the
 * first step did. So a refusal is the event named `refused` and not a status, which is the
 * price of answering more than once over one request.
 *
 * Frames are split on a blank line and a frame's data is one line of JSON, which is what
 * makes this safe to do by hand rather than through `EventSource`: that one only speaks
 * GET, and a question is a POST. The trailing part of the buffer is kept rather than parsed
 * because a chunk boundary falls wherever the network put it, not where a frame ends.
 */
async function heard(stream: ReadableStream<Uint8Array>, watching?: (step: Step) => void) {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let answered: Answered | null = null;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const [name, body] = read(frame);
      if (name === "step") watching?.(body as Step);
      if (name === "answer") answered = body as Answered;
      if (name === "refused") throw refusedBy(body as Refusing, "the server refused the question");
    }
  }
  if (answered === null) {
    // The stream ended without saying anything. Not a validator's list and not a status
    // either, so it is shown as a server that failed without saying what failed.
    throw new Refused(["the answer never arrived, the connection ended first"], { said: false });
  }
  return answered;
}

/** One frame, as its event name and its body. A frame this does not recognise comes back
 * under a name nothing acts on, which is how a server that learns a fourth event stops
 * short of breaking a browser that only knows three. */
function read(frame: string): [string, unknown] {
  let name = "";
  let data = "";
  for (const line of frame.split("\n")) {
    if (line.startsWith("event: ")) name = line.slice(7);
    if (line.startsWith("data: ")) data = line.slice(6);
  }
  try {
    return [name, JSON.parse(data)];
  } catch {
    return ["", null];
  }
}

/**
 * What a model call cost, in tokens and in billed requests.
 *
 * Every attempt of the retry loop added up, not the one that worked: what a person paid
 * for was the loop. `calls` is that loop made visible — a question that took three tries
 * costing three times one that took one is the thing worth showing.
 *
 * Zero calls means the model was not asked, which is the common case for a critique and is
 * different from a call that reported no usage.
 */
export type Cost = { calls: number; prompt: number; completion: number; total: number };

/** What a spec produced: itself, its rows, and what the question cost where a model was
 * asked one. Running a spec by hand reaches no model and carries no cost. */
export type Answered = { spec: Spec; rows: Row[]; cost?: Cost };

/** What a rule refuses about a spec, and the spec suggested in its place.
 *
 * `findings` empty means there is nothing to say and no model was asked. `spec` null with
 * findings present means a rule refused something and nothing survived the rules as a
 * replacement, which `errors` says. Nothing is applied here: the suggestion is a second
 * spec beside the one on screen, and running it is `execute` like any other. */
export type Suggestion = {
  findings: string[];
  spec: Spec | null;
  errors: string[];
  cost?: Cost;
};

export const critique = (spec: Spec): Promise<Suggestion> =>
  json("/api/critique", posted({ spec }));

/** Every saved dashboard, as a name and a tile count. No spec comes back here: this is
 * what a menu is drawn from. */
export const getDashboards = (): Promise<{ dashboards: Saved[] }> => json("/api/dashboards");

export const getDashboard = (name: string): Promise<Dashboard> =>
  json(`/api/dashboards/${encodeURIComponent(name)}`);

/** Save under a name, replacing whatever it held. The tiles are refused whole where one of
 * them does not validate, and the refusal carries the validator's own words.
 *
 * `asStored` is what strips the id the interface arranges by, so what the store receives is
 * a spec and a width and nothing this browser invented. */
export const saveDashboard = (
  name: string,
  tiles: Tile[],
  filters: Across = [],
): Promise<Dashboard> =>
  json(`/api/dashboards/${encodeURIComponent(name)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tiles: asStored(tiles), filters }),
  });

export const deleteDashboard = (name: string): Promise<unknown> =>
  json(`/api/dashboards/${encodeURIComponent(name)}`, { method: "DELETE" });

/** How to get from one table to another, as the joins a spec carries. The resolver
 * decides; a browser that worked this out from the relationship list would be a second
 * opinion that can disagree with the one the query is built from. */
export const getJoinPath = (left: string, right: string): Promise<{ joins: Join[] }> =>
  json(`/api/join-path?left=${encodeURIComponent(left)}&right=${encodeURIComponent(right)}`);
