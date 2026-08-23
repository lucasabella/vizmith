import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { ColumnProfile } from "../api";
import { fromProfiles } from "../panels/field-data";
import DataBrowser from "./DataBrowser";

const column = (name: string, samples: string[]): ColumnProfile => ({
  name,
  type: "string",
  null_rate: 0,
  distinct_count: samples.length,
  distinct_count_exact: true,
  minimum: null,
  maximum: null,
  samples,
});

const tables = fromProfiles([
  { table: "vizmith.shop.orders", row_count: 12, columns: [column("country", ["NL", "DE"])] },
  { table: "vizmith.shop.customers", row_count: 8, columns: [column("segment", ["Trade"])] },
]);

describe("the data browser", () => {
  it("shows profiles and states what stays local", () => {
    render(<DataBrowser tables={tables} failure={null} connected />);

    expect(screen.getByRole("heading", { name: "Data" })).toBeDefined();
    expect(screen.getByText("orders.country")).toBeDefined();
    expect(screen.getByText(/individual rows and query results/)).toBeDefined();
  });

  it("moves the profile with the selected table", async () => {
    render(<DataBrowser tables={tables} failure={null} connected />);

    await userEvent.setup().click(screen.getByRole("button", { name: /customers/ }));

    expect(screen.getByText("customers.segment")).toBeDefined();
    expect(screen.getByText("Trade")).toBeDefined();
  });
});
