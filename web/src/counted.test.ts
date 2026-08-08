import { describe, expect, it } from "vitest";
import { counted } from "./counted";

describe("a count and what is being counted", () => {
  it("says one of a thing in the singular", () => {
    // The bug this closes. The one result shape that ever shows a count of one is a
    // figure — no dimension, one output column, one row — which is the answer a person
    // reads most closely, and the meta line under it said `1 rows`.
    expect(counted(1, "row")).toBe("1 row");
    expect(counted(1, "tile")).toBe("1 tile");
  });

  it("says everything else in the plural, zero included", () => {
    expect(counted(0, "row")).toBe("0 rows");
    expect(counted(30, "row")).toBe("30 rows");
  });

  it("takes a plural that is not an s rather than guessing one", () => {
    expect(counted(2, "entry", "entries")).toBe("2 entries");
    expect(counted(1, "entry", "entries")).toBe("1 entry");
  });
});
