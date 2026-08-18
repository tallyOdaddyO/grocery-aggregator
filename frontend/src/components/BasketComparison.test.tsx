import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { BasketPlan, CompareBasketResponse, PriceData } from "../types/api";
import { BasketComparison } from "./BasketComparison";

const NOW = new Date("2026-08-18T12:00:00Z");

function price(cents: number | null): PriceData {
  return {
    sticker_price_cents: cents,
    unit_price_cents: cents,
    unit_measure: "lb",
    provenance: {
      status: "verified_online",
      timestamp: NOW.toISOString(),
      source_url: null,
      verification_method: "verified_online",
      is_fresh: true,
    },
  };
}

function line(query: string, retailer: any, cents: number, notes: string[] = []) {
  return {
    query,
    quantity: 1,
    product_id: `${retailer}:${query}`,
    retailer,
    title: `${query} item`,
    size_raw: "12 oz",
    price: price(cents),
    line_total_cents: cents,
    notes,
  };
}

const completePlan: BasketPlan = {
  strategy: "single_store",
  trips: [
    {
      retailer: "fresco_y_mas",
      store_number: "0512",
      items: [line("milk", "fresco_y_mas", 369), line("cola", "fresco_y_mas", 249)],
      subtotal_cents: 618,
    },
  ],
  total_cents: 618,
  item_count: 2,
  stop_count: 1,
};

const splitPlan: BasketPlan = {
  strategy: "split",
  trips: [
    {
      retailer: "fresco_y_mas",
      store_number: "0512",
      items: [line("milk", "fresco_y_mas", 369)],
      subtotal_cents: 369,
    },
    {
      retailer: "presidente",
      store_number: "PS-27",
      items: [line("cola", "presidente", 200, ["multi_buy_required"])],
      subtotal_cents: 200,
    },
  ],
  total_cents: 569,
  item_count: 2,
  stop_count: 2,
};

function response(overrides: Partial<CompareBasketResponse> = {}): CompareBasketResponse {
  return {
    zip_code: "33009",
    is_complete: true,
    connector_health: [],
    cheapest_complete: completePlan,
    cheapest_split: splitPlan,
    savings_cents: 49,
    unavailable_items: [],
    ...overrides,
  };
}

describe("BasketComparison", () => {
  it("shows both plans side by side with their totals", () => {
    render(<BasketComparison data={response()} now={NOW} />);
    expect(within(screen.getByTestId("plan-complete")).getByText("$6.18")).toBeInTheDocument();
    expect(within(screen.getByTestId("plan-split")).getByText("$5.69")).toBeInTheDocument();
  });

  it("states the trade-off in stops, not just money", () => {
    render(<BasketComparison data={response()} now={NOW} />);
    expect(within(screen.getByTestId("plan-complete")).getByText(/1 stop\b/)).toBeInTheDocument();
    expect(within(screen.getByTestId("plan-split")).getByText(/2 stops/)).toBeInTheDocument();
  });

  it("shows what splitting actually saves", () => {
    render(<BasketComparison data={response()} now={NOW} />);
    expect(screen.getByTestId("savings")).toHaveTextContent("$0.49");
  });

  it("says plainly when no single store can fulfil the basket", () => {
    render(
      <BasketComparison
        data={response({ cheapest_complete: null, savings_cents: null })}
        now={NOW}
      />,
    );
    expect(screen.getByTestId("plan-complete")).toHaveTextContent(
      /no single store nearby stocks every item/i,
    );
    // The split plan must still be usable.
    expect(within(screen.getByTestId("plan-split")).getByText("$5.69")).toBeInTheDocument();
    expect(screen.queryByTestId("savings")).not.toBeInTheDocument();
  });

  it("groups split lines under the store you would visit", () => {
    render(<BasketComparison data={response()} now={NOW} />);
    const trips = within(screen.getByTestId("plan-split")).getAllByTestId("trip");
    expect(trips).toHaveLength(2);
    expect(trips[0]).toHaveTextContent(/fresco y m/i);
    expect(trips[1]).toHaveTextContent(/presidente/i);
  });

  it("surfaces a multi-buy caveat on the line it applies to", () => {
    render(<BasketComparison data={response()} now={NOW} />);
    expect(screen.getByText(/requires buying more than one/i)).toBeInTheDocument();
  });

  it("lists items nothing nearby can supply", () => {
    render(
      <BasketComparison
        data={response({
          unavailable_items: [
            { query: "saffron", quantity: 1, reason: "No retailer returned a match." },
          ],
        })}
        now={NOW}
      />,
    );
    const unavailable = screen.getByTestId("unavailable");
    expect(unavailable).toHaveTextContent("saffron");
    expect(unavailable).toHaveTextContent("No retailer returned a match.");
  });

  it("warns when the comparison is incomplete", () => {
    render(
      <BasketComparison
        data={response({
          is_complete: false,
          connector_health: [
            {
              retailer: "walmart",
              status: "degraded",
              latency_ms: 306,
              error_reason: "Walmart did not respond within 0.3s.",
            },
          ],
        })}
        now={NOW}
      />,
    );
    const warning = screen.getByRole("status");
    expect(warning).toHaveTextContent(/may be incomplete/i);
    expect(warning).toHaveTextContent(/walmart/i);
  });

  it("does not warn when every retailer reported", () => {
    render(<BasketComparison data={response()} now={NOW} />);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("renders nothing purchasable without crashing", () => {
    render(
      <BasketComparison
        data={response({
          cheapest_complete: null,
          cheapest_split: null,
          savings_cents: null,
          unavailable_items: [
            { query: "unobtainium", quantity: 1, reason: "No retailer returned a match." },
          ],
        })}
        now={NOW}
      />,
    );
    expect(screen.getByTestId("unavailable")).toHaveTextContent("unobtainium");
    expect(screen.getByTestId("plan-split")).toHaveTextContent(/nothing.*could be sourced/i);
  });
});
