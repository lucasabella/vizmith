import type { ColumnProfile, TableProfile, TableShape } from "../api";

/**
 * What the Fields panel draws, from either of the two things the server can answer with.
 *
 * The panel is filled twice. `/api/shape` lands first and costs no statement — it is names
 * and types, which is what the tree renders and all a drag reads — and `/api/tables` lands
 * behind it with the profiles. So every row in the tree has to be drawable from the half
 * that arrived, and has to say which half that is rather than showing an absent figure as
 * a real one.
 *
 * Two nulls carry that, and both are the point rather than an inconvenience. `row_count` is
 * null until the profile lands, because the count comes from a pass over the table and
 * showing a zero in the meantime is a lie about the data. `profile` is null for the same
 * window, because the Fields panel is the screen that proves what the model was allowed to
 * see: a blank where a sample list goes reads as a column with no values, which is a
 * different and much worse statement than "not read yet".
 */
export type ColumnFields = { name: string; type: string; profile: ColumnProfile | null };

export type TableFields = {
  table: string;
  row_count: number | null;
  columns: ColumnFields[];
};

/** The shape, as the tree before anything has been profiled. */
export const fromShape = (tables: TableShape[]): TableFields[] =>
  tables.map((table) => ({
    table: table.table,
    row_count: null,
    columns: table.columns.map((column) => ({ ...column, profile: null })),
  }));

/** The profiles, as the tree once they have landed. */
export const fromProfiles = (tables: TableProfile[]): TableFields[] =>
  tables.map((table) => ({
    table: table.table,
    row_count: table.row_count,
    columns: table.columns.map((column) => ({
      name: column.name,
      type: column.type,
      profile: column,
    })),
  }));

/**
 * The two, where both have arrived.
 *
 * The profiles win wherever they exist, and the shape is what fills the gaps. That is not
 * only for the window between the two requests: a table that a profile refused — a view
 * `DESCRIBE DETAIL` will not describe, a table the credential can list and not scan — is
 * a table the shape still has, and dropping it from the tree because its figures could not
 * be read would be the panel quietly disagreeing with the schema.
 *
 * Order is the shape's, which is the source's own listing order, so the tree does not
 * rearrange itself under somebody's hand as the profiles land.
 */
export function merged(shape: TableFields[], profiled: TableFields[]): TableFields[] {
  const byName = new Map(profiled.map((table) => [table.table, table]));
  const extra = profiled.filter((table) => !shape.some((known) => known.table === table.table));
  return [...shape.map((table) => byName.get(table.table) ?? table), ...extra];
}
