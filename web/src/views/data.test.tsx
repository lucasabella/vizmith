import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { Relationship } from "../api";
import { Sections } from "./Data";

const drawn = (relationships: Relationship[]) =>
  renderToStaticMarkup(
    <Sections relationships={relationships} working={null} onAnswer={() => {}} />,
  );

const relationship = (over: Partial<Relationship> = {}): Relationship => ({
  left_table: "shop.orders",
  left_column: "customer_id",
  right_table: "shop.customers",
  right_column: "id",
  kind: "suggested",
  state: "open",
  ...over,
});

describe("the relationships screen", () => {
  it("asks about a suggestion and states what the source declares", () => {
    const markup = drawn([
      relationship(),
      relationship({ left_column: "carrier_id", kind: "declared", state: "confirmed" }),
    ]);

    expect(markup).toContain("Confirm");
    expect(markup).toContain("Suggested");
    expect(markup).toContain("Declared");
  });

  it("says a lakehouse declares nothing where nothing is declared", () => {
    const markup = drawn([relationship()]);

    expect(markup).toContain("usual for a lakehouse");
  });

  /**
   * The case PostgreSQL produces and a lakehouse never does: enforced foreign keys, present
   * on any schema with a design, so the source has answered its own question. Two sections
   * saying nothing is here, above a long list of facts, reads as broken — and it is the
   * good case.
   */
  it("says why there is nothing to answer where the source declares its own keys", () => {
    const markup = drawn([
      relationship({ kind: "declared", state: "confirmed" }),
      relationship({ left_column: "order_id", kind: "declared", state: "confirmed" }),
    ]);

    expect(markup).toContain("This source declares its own keys");
    expect(markup).toContain("Every relationship below is one the source states for itself");
    expect(markup).not.toContain("You have not confirmed a suggestion yet");
  });

  it("keeps the ordinary wording where the source declares nothing", () => {
    const markup = drawn([relationship()]);

    expect(markup).toContain("You have not confirmed a suggestion yet");
    expect(markup).not.toContain("This source declares its own keys");
  });
});
