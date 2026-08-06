/**
 * The rules this browser holds a second copy of, asked the same questions the server is.
 *
 * Each mirror exists for a reason — the interface stops a person pressing Save to find out,
 * the wells write a reference the validator will resolve, ECharts paints onto a canvas and
 * cannot read a custom property — and each was free to drift, with nothing failing when it
 * did. One pair already had: the store refused a name holding a control character and the
 * browser did not, so a name with a tab in it passed the check that exists to prevent
 * exactly that.
 *
 * The cases live in `tests/fixtures/mirrors`, read from here and from pytest, so a rule
 * added on one side and not the other fails on both.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { SERIES } from "./chart/option";
import { COLUMNS, NAME_LIMIT, TILE_LIMIT, nameProblem } from "./dashboard/dashboard";
import { namesTable, outputColumns, type Query } from "./spec/spec";

const read = (path: string) => readFileSync(fileURLToPath(new URL(path, import.meta.url)), "utf8");
const names = JSON.parse(read("../../tests/fixtures/mirrors/names.json"));
const references = JSON.parse(read("../../tests/fixtures/mirrors/references.json"));

type NameCase = { name: string; saveable: boolean; why: string };
type ReferenceCase = { table: string; reference: string; names: boolean };
type ColumnsCase = { query: Query; columns: string[]; why: string };

describe("a dashboard name, judged the way the store judges it", () => {
  it.each(names.cases as NameCase[])("$why", (one) => {
    expect(nameProblem(one.name) === null).toBe(one.saveable);
  });
});

describe("a reference to a table, resolved the way the validator resolves it", () => {
  it.each(references.names_table as ReferenceCase[])(
    "$reference names $table: $names",
    (one) => {
      expect(namesTable(one.table, one.reference)).toBe(one.names);
    },
  );
});

describe("the result set contract", () => {
  it.each(references.output_columns as ColumnsCase[])("$why", (one) => {
    expect(outputColumns(one.query)).toEqual(one.columns);
  });
});

describe("the series colours", () => {
  it("are the tokens the rest of the interface wears, in the same order", () => {
    // The order is the colourblind safety mechanism rather than a preference, so it is the
    // order that is asserted and not the set.
    const tokens = read("./styles/tokens.css");
    const declared = [...tokens.matchAll(/--series-(\d+):\s*(#[0-9a-fA-F]{6})/g)]
      .sort((one, two) => Number(one[1]) - Number(two[1]))
      .map((match) => match[2].toLowerCase());

    expect(declared).toEqual([...SERIES]);
  });
});

/**
 * The other half of the same copy. `option.ts` writes out the surface, the three inks, the
 * two rules and the two font stacks for the same reason it writes out the series order —
 * ECharts paints onto a canvas and cannot read a custom property — and only the order had
 * anything holding it. The chrome was free to drift, and drift here is a chart wearing an
 * ink the rest of the screen stopped using, which no test and no eye catches: the canvas
 * and the DOM around it are never the same pixel.
 *
 * Read as text, the way the dashboard constants below are, because these are module
 * private and exporting them to be asserted would widen the file's surface for the sake of
 * this test.
 */
describe("the chart chrome, against the tokens it copies", () => {
  const tokens = read("./styles/tokens.css");
  const option = read("./chart/option.ts");

  const token = (name: string) =>
    new RegExp(`--${name}:\\s*([^;]+);`).exec(tokens)?.[1].trim().toLowerCase();
  const constant = (name: string) =>
    new RegExp(`^const ${name} = (["'])(.*)\\1;`, "m").exec(option)?.[2].trim().toLowerCase();

  it.each([
    ["SURF", "surf"],
    ["INK", "ink"],
    ["INK_2", "ink-2"],
    ["INK_3", "ink-3"],
    ["RULE", "rule"],
    ["RULE_2", "rule-2"],
    ["UI", "ui"],
    ["MONO", "mono"],
  ])("%s is --%s", (name, custom) => {
    // Both sides read rather than assumed, so a constant that was renamed away fails here
    // as a missing mirror rather than passing as two undefineds that agree.
    expect(constant(name)).toBeDefined();
    expect(token(custom)).toBeDefined();
    expect(constant(name)).toBe(token(custom));
  });
});

describe("the dashboard constants", () => {
  it("hold what the store holds", () => {
    const python = read("../../src/vizmith/dashboards.py");
    const value = (name: string) => Number(new RegExp(`^${name} = (\\d+)`, "m").exec(python)?.[1]);

    expect(value("COLUMNS")).toBe(COLUMNS);
    expect(value("TILE_LIMIT")).toBe(TILE_LIMIT);
    expect(value("NAME_LIMIT")).toBe(NAME_LIMIT);
  });
});
