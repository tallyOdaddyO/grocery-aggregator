import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { PriceData } from "../types/api";
import { PriceTag } from "./PriceTag";

const NOW = new Date("2026-08-18T12:00:00Z");

function price(overrides: Partial<PriceData> = {}): PriceData {
  return {
    sticker_price_cents: 1999,
    unit_price_cents: 50,
    unit_measure: "ct",
    provenance: {
      status: "verified_online",
      timestamp: new Date(NOW.getTime() - 18 * 60_000).toISOString(),
      source_url: null,
      verification_method: "verified_online",
      is_fresh: true,
    },
    ...overrides,
  };
}

describe("PriceTag", () => {
  it("shows sticker price and unit price as separate figures", () => {
    render(<PriceTag price={price()} now={NOW} />);
    expect(screen.getByTestId("sticker-price")).toHaveTextContent("$19.99");
    expect(screen.getByTestId("unit-price")).toHaveTextContent("$0.50/count");
  });

  it("never presents the unit price as the price you pay", () => {
    render(<PriceTag price={price()} now={NOW} />);
    const sticker = screen.getByTestId("sticker-price").textContent ?? "";
    expect(sticker).not.toContain("/");
  });

  it("omits the unit price when the package size was not parseable", () => {
    render(
      <PriceTag price={price({ unit_price_cents: null, unit_measure: "unknown" })} now={NOW} />,
    );
    expect(screen.queryByTestId("unit-price")).not.toBeInTheDocument();
  });

  it("says the item is stocked but unpriced rather than showing $0.00", () => {
    render(
      <PriceTag
        price={price({
          sticker_price_cents: null,
          unit_price_cents: null,
          unit_measure: "unknown",
          provenance: {
            status: "no_price_published",
            timestamp: NOW.toISOString(),
            source_url: null,
            verification_method: "no_price_published",
            is_fresh: false,
          },
        })}
        now={NOW}
      />,
    );
    expect(screen.getByText(/no published price/i)).toBeInTheDocument();
    expect(screen.queryByText("$0.00")).not.toBeInTheDocument();
  });

  it("shows how long ago the price was checked", () => {
    render(<PriceTag price={price()} now={NOW} />);
    expect(screen.getByText("Checked 18 minutes ago")).toBeInTheDocument();
  });

  it("flags a stale price instead of presenting it as current", () => {
    render(
      <PriceTag
        price={price({
          provenance: {
            status: "verified_online",
            timestamp: new Date(NOW.getTime() - 5 * 86_400_000).toISOString(),
            source_url: null,
            verification_method: "verified_online",
            is_fresh: false,
          },
        })}
        now={NOW}
      />,
    );
    expect(screen.getByTestId("freshness")).toHaveAttribute("data-fresh", "false");
    expect(screen.getByText(/out of date/i)).toBeInTheDocument();
  });

  it.each([
    ["verified_in_store", "Verified in store"],
    ["verified_online", "Retailer price, not shelf-checked"],
    ["delivery_price", "Delivery price, may differ in store"],
    ["estimated", "Estimated from a circular"],
  ] as const)("labels %s distinctly", (method, label) => {
    render(
      <PriceTag
        price={price({
          provenance: {
            status: method,
            timestamp: NOW.toISOString(),
            source_url: null,
            verification_method: method,
            is_fresh: true,
          },
        })}
        now={NOW}
      />,
    );
    expect(screen.getByTestId("provenance")).toHaveTextContent(label);
  });
});
