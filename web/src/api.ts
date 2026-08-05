/**
 * What the server answers, and the shapes it answers in.
 *
 * Every type here mirrors something Python produced. Nothing in the browser recomputes
 * one: a profile is the profiler's, a relationship is the catalog's, and a join path is
 * the resolver's. The panels show what came back and add no opinion about it.
 */

import type { Row, Spec } from "./chart/option";
import type { Dashboard, Saved, Tile } from "./dashboard/dashboard";
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

/** The qualified name of every table. A row count is not here: it is one of the figures a
 * profile holds, and the panel reads the profiles anyway. */
export const getTables = (): Promise<{ tables: string[] }> => json("/api/tables");

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

/** Every saved dashboard, as a name and a tile count. No spec comes back here: this is
 * what a menu is drawn from. */
export const getDashboards = (): Promise<{ dashboards: Saved[] }> => json("/api/dashboards");

export const getDashboard = (name: string): Promise<Dashboard> =>
  json(`/api/dashboards/${encodeURIComponent(name)}`);

/** Save under a name, replacing whatever it held. The tiles are refused whole where one of
 * them does not validate, and the refusal carries the validator's own words. */
export const saveDashboard = (name: string, tiles: Tile[]): Promise<Dashboard> =>
  json(`/api/dashboards/${encodeURIComponent(name)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tiles }),
  });

export const deleteDashboard = (name: string): Promise<unknown> =>
  json(`/api/dashboards/${encodeURIComponent(name)}`, { method: "DELETE" });

/** How to get from one table to another, as the joins a spec carries. The resolver
 * decides; a browser that worked this out from the relationship list would be a second
 * opinion that can disagree with the one the query is built from. */
export const getJoinPath = (left: string, right: string): Promise<{ joins: Join[] }> =>
  json(`/api/join-path?left=${encodeURIComponent(left)}&right=${encodeURIComponent(right)}`);
