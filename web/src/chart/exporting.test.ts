import { describe, expect, it } from "vitest";
import { csv, field, fileName } from "./exporting";
import type { Row } from "./option";
import type { Spec } from "../spec/spec";

/**
 * What leaves the tab, and what must not leave it intact.
 *
 * A CSV is where two escaping questions live, and the second is the one that gets
 * forgotten. The first is RFC 4180 and is about the file being readable at all. The second
 * is that a spreadsheet runs a cell that starts like a formula, which is the same rule as
 * the tooltip drawn in `richText`: a value out of somebody's warehouse never becomes
 * something that executes.
 */

const spec = (title?: string): Spec =>
  ({
    title,
    chart: { mark: "bar", encoding: { y: { field: "revenue", type: "quantitative" } } },
  }) as Spec;

describe("one value as a CSV field", () => {
  it.each([
    ["plain text is itself", "Netherlands", "Netherlands"],
    ["a comma makes it a quoted field", "Pencils, HB", '"Pencils, HB"'],
    ["a quote is doubled inside the quotes", 'the "good" one', '"the ""good"" one"'],
    ["a newline stays in one field", "two\nlines", '"two\nlines"'],
    ["a carriage return counts as one too", "two\rlines", '"two\rlines"'],
  ])("%s", (_why, value, written) => {
    expect(field(value)).toBe(written);
  });

  it.each(["=1+1", "+SUM(A1)", "@import", "=cmd|'/c calc'!A1"])(
    "a value a spreadsheet would run, %s, arrives as text",
    (value) => {
      expect(field(value).replace(/^"|"$/g, "").startsWith("'")).toBe(true);
    },
  );

  it("does not put an apostrophe in front of a negative number", () => {
    // `-` opens a formula and also opens every negative number there is. A guard that
    // cannot tell them apart turns a numeric column into a column of text.
    expect(field(-5)).toBe("-5");
    expect(field("-5")).toBe("-5");
    expect(field("-1.5e3")).toBe("-1.5e3");
  });

  it("writes a null as an empty field and a boolean as itself", () => {
    expect(field(null)).toBe("");
    expect(field(true)).toBe("true");
    expect(field(0)).toBe("0");
  });
});

describe("the rows as a file", () => {
  const rows: Row[] = [
    { country: "Netherlands", revenue: 1200.5 },
    { country: "Spain, mainland", revenue: null },
  ];

  it("is a header and a line per row, in the result set's column order", () => {
    expect(csv(rows)).toBe(
      "country,revenue\r\nNetherlands,1200.5\r\n" + '"Spain, mainland",\r\n',
    );
  });

  it("takes its columns from the row and not from the encoding", () => {
    // The query may produce more columns than the chart binds, and the Table tab shows all
    // of them. A file that carried three of six would be a quieter version of the same bug.
    expect(csv([{ a: 1, b: 2, c: 3 }]).split("\r\n")[0]).toBe("a,b,c");
  });

  it("is empty where there are no rows", () => {
    expect(csv([])).toBe("");
  });
});

describe("the name a file is saved under", () => {
  it("comes from the title, reduced to something every filesystem takes", () => {
    expect(fileName(spec("Revenue per country, 2026"), "csv")).toBe("revenue-per-country-2026.csv");
  });

  it("falls back to what the chart measures where there is no title", () => {
    expect(fileName(spec(), "png")).toBe("revenue.png");
  });

  it("survives a title that is only punctuation", () => {
    // A title comes from a model or a person, so it can be anything a string can be, and
    // "..csv" or "/.png" is not a file name.
    expect(fileName(spec("///"), "csv")).toBe("chart.csv");
  });

  it("does not carry a path out of a title", () => {
    expect(fileName(spec("../../etc/passwd"), "csv")).toBe("etc-passwd.csv");
  });
});
