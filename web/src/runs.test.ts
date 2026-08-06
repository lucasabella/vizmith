import { describe, expect, it } from "vitest";
import { sequence } from "./runs";

describe("two runs in flight", () => {
  it("lets the latest one write and nobody else", () => {
    const runs = sequence();

    const first = runs.start();
    const second = runs.start();

    expect(first()).toBe(false);
    expect(second()).toBe(true);
  });

  it("drops an answer that arrives after a newer run was started, whichever finished first", () => {
    // The order the answers come back in is not the order they were asked in: a second
    // query may be faster than the first, especially when the first is what woke the
    // warehouse. What decides is which run is still the one being waited for.
    const runs = sequence();
    const first = runs.start();
    const second = runs.start();
    const written: string[] = [];

    if (second()) written.push("second");
    if (first()) written.push("first");

    expect(written).toEqual(["second"]);
  });

  it("lets one run on its own write, so nothing is dropped when nothing overlaps", () => {
    const runs = sequence();

    const only = runs.start();

    expect(only()).toBe(true);
    expect(only()).toBe(true);
  });

  it("keeps saying yes to the latest until a newer one starts, so working clears once", () => {
    const runs = sequence();
    const first = runs.start();

    expect(first()).toBe(true);
    const second = runs.start();

    expect(first()).toBe(false);
    expect(second()).toBe(true);
  });
});
