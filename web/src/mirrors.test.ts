// @vitest-environment node
//
// This one reads the spec fixtures off disk, and a jsdom `URL` is not a file URL.
// The default environment is jsdom now, so a test that only needs Node says so
// rather than the whole suite paying for the one that does not.
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
import {
  CHANNEL_TYPES,
  COMPARISONS,
  DIRECTIONS,
  FNS,
  JOIN_TYPES,
  MARKS,
  OPS,
  UNITS,
  namesTable,
  outputColumns,
  type Query,
} from "./spec/spec";
import { SAID } from "./outcome";

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
  // `export` is optional: `SURF` is exported so a PNG can be taken on the surface the
  // chart is drawn on, and the mirror is about the value rather than about the visibility.
  const constant = (name: string) =>
    new RegExp(`^(?:export )?const ${name} = (["'])(.*)\\1;`, "m").exec(option)?.[2].trim().toLowerCase();

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

/**
 * The document is a third copy, and it is the one a contributor reads before touching
 * anything. `docs/design.md` quotes every series colour and the contrast each has against
 * `--surf`, because a rule about which slots are legal is not a rule unless it says which
 * slots. Those numbers are printed by `docs/palette.py` off `tokens.css`, so the failure
 * mode is a colour changed in the stylesheet and a document still describing the old one —
 * a contributor checking their new chart against a table that is quietly wrong.
 *
 * The contrast is recomputed here rather than parsed from the script, so the document is
 * checked against the tokens and not against the thing that generated it.
 */
describe("the design document, against the tokens it documents", () => {
  const tokens = read("./styles/tokens.css");
  const design = read("../../docs/design.md");

  const value = (name: string) =>
    new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6})\\s*;`).exec(tokens)?.[1].toLowerCase();

  const luminance = (colour: string) => {
    const channels = [1, 3, 5].map((at) => parseInt(colour.slice(at, at + 2), 16) / 255);
    const [red, green, blue] = channels.map((one) =>
      one <= 0.04045 ? one / 12.92 : ((one + 0.055) / 1.055) ** 2.4,
    );
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
  };
  const contrast = (one: string, two: string) => {
    const [first, second] = [luminance(one), luminance(two)];
    return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05);
  };

  it.each([1, 2, 3, 4, 5, 6, 7, 8])("names slot %i and the colour the token holds", (slot) => {
    const colour = value(`series-${slot}`);
    expect(colour).toBeDefined();
    // The row of the order table: a slot, its token, its hex.
    expect(design).toContain(`| ${slot} | \`--series-${slot}\` | \`${colour}\` |`);
  });

  it("quotes each series colour's contrast against --surf as it is", () => {
    const surf = value("surf");
    expect(surf).toBeDefined();
    for (let slot = 1; slot <= 8; slot += 1) {
      const colour = value(`series-${slot}`) as string;
      const stated = new RegExp(`\\| ${slot} \\| \`${colour}\` \\| ([0-9.]+) \\|`).exec(design);
      expect(stated, `docs/design.md has no contrast row for slot ${slot}`).not.toBeNull();
      expect(Number(stated?.[1])).toBeCloseTo(contrast(colour, surf as string), 2);
    }
  });

  it("names the three slots the Table tab exists for", () => {
    // The escape hatch is quoted by slot number in Table.tsx and Visual.tsx, so which
    // slots fail 3:1 is load-bearing prose rather than a detail of the table above it.
    const surf = value("surf") as string;
    const failing = [1, 2, 3, 4, 5, 6, 7, 8].filter(
      (slot) => contrast(value(`series-${slot}`) as string, surf) < 3,
    );
    expect(failing).toEqual([3, 4, 5]);
    expect(design).toContain("Three slots are under 3:1");
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

/**
 * Which parts can refuse, as the server names them.
 *
 * `spoke` is a field the server sets and the browser branches on, so the two hold the same
 * closed set in two languages. What drift looks like here is a fifth part learning to
 * refuse — a cache, a second model call, whatever it turns out to be — and every interface
 * that catches it falling through to the sentence for a refusal it is not: `refusal()` in
 * `outcome.ts` reads an unknown name as `undefined` and heads it "What the validator said",
 * which is the wrong heading and the wrong next move, silently.
 *
 * Only `SAID` is asserted, because TypeScript already ties the rest to it: `SAID` is typed
 * `Record<Spoke, …>`, so a key here that the union does not have, or a member of the union
 * with no key, is a compile error rather than something this test has to catch twice.
 */
describe("the parts that can refuse", () => {
  it("are the parts the browser has a sentence for", () => {
    const python = read("../../src/vizmith/api.py");
    // Two ways the server writes one. Most refusals go through `refused(spoke, …)`; the
    // rationing handler writes the body itself, because it is an exception handler and has
    // a `Retry-After` header to set as well.
    const named = [
      ...[...python.matchAll(/\brefused\(\s*"([a-z]+)"/g)].map((match) => match[1]),
      ...[...python.matchAll(/"spoke":\s*"([a-z]+)"/g)].map((match) => match[1]),
    ];

    expect(named.length).toBeGreaterThan(0);
    expect([...new Set(named)].sort()).toEqual(Object.keys(SAID).sort());
  });
});

/**
 * The grammar, which is the copy that matters most and was the one nothing held.
 *
 * `spec/v1/spec.schema.json` is the grammar and the only judge of a spec. The browser
 * writes its closed sets out again — a mark goes in a control, an operator goes in a filter
 * the wells build, a channel type picks an axis — and every one of them was a union typed by
 * hand with nothing failing when it disagreed. What disagreement looks like is not a crash:
 * it is a spec this interface wrote, accepted by the checker, accepted by the wells, and
 * refused by the validator after the round trip.
 *
 * There is one copy of each now, in `spec/spec.ts`, which is what this holds to the schema.
 * The renderer had two more and has none: `option.ts` imports the grammar's types, and its
 * two lookups are `Record`s keyed by them, so a mark or a channel type added here is a
 * compile error in the renderer until it is drawn. That is stricter than a mirror and it is
 * why the assertions that read this file as text are gone.
 *
 * Read as sets rather than in order, unlike the colour mirror above: the order of these is
 * the order a person reads them in a menu, which is a decision for the interface, while
 * which values exist is the schema's.
 */
describe("the grammar's closed sets, against the schema that is the grammar", () => {
  const schema = JSON.parse(read("../../src/vizmith/spec/v1/spec.schema.json"));

  /** One enum, by the definition it belongs to. Read rather than assumed: a definition
   * renamed away answers `undefined` and fails as a missing mirror, rather than passing as
   * two empty lists that agree. */
  const enumOf = (definition: string, property: string): string[] =>
    schema.$defs[definition].properties[property].enum;

  it.each([
    ["chart.mark", enumOf("chart", "mark"), MARKS],
    ["channel.type", enumOf("channel", "type"), CHANNEL_TYPES],
    ["aggregate.fn", enumOf("aggregate", "fn"), FNS],
    ["select_item.truncate", enumOf("select_item", "truncate"), UNITS],
    ["join.type", enumOf("join", "type"), JOIN_TYPES],
    ["order_by.direction", enumOf("order_by", "direction"), DIRECTIONS],
    ["limit_by.direction", schema.$defs.query.properties.limit_by.properties.direction.enum, DIRECTIONS],
    ["filter.op", enumOf("filter", "op"), OPS],
    ["having.op", enumOf("having", "op"), COMPARISONS],
  ])("%s is what spec.ts holds", (_name, declared, held) => {
    expect(declared.length).toBeGreaterThan(0);
    expect([...declared].sort()).toEqual([...held].sort());
  });
});
