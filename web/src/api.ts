/**
 * What the server answers, and the shapes it answers in.
 *
 * Every type here mirrors something Python produced. Nothing in the browser recomputes
 * one: a profile is the profiler's, a relationship is the catalog's, and a join path is
 * the resolver's. The panels show what came back and add no opinion about it.
 */

import type { Row, Spec } from "./chart/option";
import { asStored, type Dashboard, type Saved, type Tile } from "./dashboard/dashboard";
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

/** A refusal, carrying the server's own words. They are what the interface shows: a
 * message written here would be a second account of something that already has one. */
export class Refused extends Error {
  readonly errors: string[];

  constructor(errors: string[]) {
    super(errors[0] ?? "the server refused the request");
    this.errors = errors;
  }
}

async function json<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Refused(body.errors ?? [`${response.status} ${response.statusText}`]);
  return body as T;
}

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
export const execute = (spec: Spec): Promise<{ spec: Spec; rows: Row[] }> =>
  json("/api/execute", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ spec }),
  });

/** What a rule refuses about a spec, and the spec suggested in its place.
 *
 * `findings` empty means there is nothing to say and no model was asked. `spec` null with
 * findings present means a rule refused something and nothing survived the rules as a
 * replacement, which `errors` says. Nothing is applied here: the suggestion is a second
 * spec beside the one on screen, and running it is `execute` like any other. */
export type Suggestion = { findings: string[]; spec: Spec | null; errors: string[] };

export const critique = (spec: Spec): Promise<Suggestion> =>
  json("/api/critique", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ spec }),
  });

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
export const saveDashboard = (name: string, tiles: Tile[]): Promise<Dashboard> =>
  json(`/api/dashboards/${encodeURIComponent(name)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tiles: asStored(tiles) }),
  });

export const deleteDashboard = (name: string): Promise<unknown> =>
  json(`/api/dashboards/${encodeURIComponent(name)}`, { method: "DELETE" });

/** How to get from one table to another, as the joins a spec carries. The resolver
 * decides; a browser that worked this out from the relationship list would be a second
 * opinion that can disagree with the one the query is built from. */
export const getJoinPath = (left: string, right: string): Promise<{ joins: Join[] }> =>
  json(`/api/join-path?left=${encodeURIComponent(left)}&right=${encodeURIComponent(right)}`);
