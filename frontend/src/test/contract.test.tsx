/**
 * Contract tests against REAL captured API responses.
 *
 * The component unit tests use hand-written fixtures, which can drift from what
 * the backend actually sends. These payloads were captured verbatim from the
 * running FastAPI app, so a wire-shape change breaks these tests rather than the
 * user's browser.
 */
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import basketJson from "./fixtures/basket.mixed.json";
import searchJson from "./fixtures/search.cheerios.json";
import { BasketComparison } from "../components/BasketComparison";
import { PriceTag } from "../components/PriceTag";
import type { CompareBasketResponse, SearchResponse } from "../types/api";

const search = searchJson as SearchResponse;
const basket = basketJson as CompareBasketResponse;

describe("search payload", () => {
  it("matches the declared TypeScript shape", () => {
    expect(search.query).toBe("cheerios");
    expect(search.zip_code).toBe("33009");
    expect(typeof search.is_complete).toBe("boolean");
    expect(search.connector_health).toHaveLength(8);
    expect(search.results.length).toBeGreaterThan(0);
  });

  it("carries provenance and a timestamp on every priced item", () => {
    for (const group of search.results) {
      for (const item of group.items) {
        expect(item.price.provenance.status).toBeTruthy();
        expect(item.price.provenance.verification_method).toBeTruthy();
        expect(Number.isNaN(Date.parse(item.price.provenance.timestamp))).toBe(false);
      }
    }
  });

  it("renders a real item without crashing", () => {
    const item = search.results[0]!.items[0]!;
    render(<PriceTag price={item.price} />);
    expect(screen.getByTestId("provenance")).toBeInTheDocument();
    expect(screen.getByTestId("sticker-price")).toBeInTheDocument();
  });

  it("labels the verified_online grade the backend actually emits", () => {
    const online = search.results
      .flatMap((g) => g.items)
      .find((i) => i.price.provenance.verification_method === "verified_online");
    expect(online, "backend no longer emits verified_online").toBeDefined();
    render(<PriceTag price={online!.price} />);
    expect(screen.getByTestId("provenance")).toHaveTextContent(
      "Retailer price, not shelf-checked",
    );
  });

  it("never renders an unpriced item as $0.00", () => {
    const unpriced = search.results
      .flatMap((g) => g.items)
      .find((i) => i.price.sticker_price_cents === null);
    expect(unpriced, "expected the distributor's unpriced item").toBeDefined();
    render(<PriceTag price={unpriced!.price} />);
    expect(screen.queryByText("$0.00")).not.toBeInTheDocument();
    expect(screen.getByTestId("sticker-price")).toHaveTextContent("Price on request");
  });
});

describe("basket payload", () => {
  it("renders the real comparison end to end", () => {
    render(<BasketComparison data={basket} />);
    expect(screen.getByTestId("plan-split")).toBeInTheDocument();
    expect(screen.getByTestId("plan-complete")).toBeInTheDocument();
  });

  it("shows the no-single-store message the backend actually returned", () => {
    expect(basket.cheapest_complete).toBeNull();
    render(<BasketComparison data={basket} />);
    expect(screen.getByTestId("plan-complete")).toHaveTextContent(
      /no single store nearby stocks every item/i,
    );
  });

  it("lists the unavailable item verbatim from the API", () => {
    render(<BasketComparison data={basket} />);
    expect(screen.getByTestId("unavailable")).toHaveTextContent("unobtainium");
  });

  it("renders one trip per store in the split plan", () => {
    render(<BasketComparison data={basket} />);
    const trips = within(screen.getByTestId("plan-split")).getAllByTestId("trip");
    expect(trips).toHaveLength(basket.cheapest_split!.stop_count);
  });

  it("line totals in the payload are exact integer cents", () => {
    for (const trip of basket.cheapest_split!.trips) {
      for (const item of trip.items) {
        expect(Number.isInteger(item.line_total_cents)).toBe(true);
        expect(item.line_total_cents).toBe(
          item.price.sticker_price_cents! * item.quantity,
        );
      }
      expect(trip.subtotal_cents).toBe(
        trip.items.reduce((sum, i) => sum + i.line_total_cents, 0),
      );
    }
  });

  it("surfaces the multi-buy caveat the backend attached", () => {
    const noted = basket
      .cheapest_split!.trips.flatMap((t) => t.items)
      .some((i) => i.notes.includes("multi_buy_required"));
    expect(noted, "backend stopped sending multi_buy_required").toBe(true);
    render(<BasketComparison data={basket} />);
    // More than one line can carry the caveat (a 2-for deal at one store and a
    // 2-x deal at another), so assert on all of them.
    const caveats = screen.getAllByText(/requires buying more than one/i);
    expect(caveats.length).toBeGreaterThanOrEqual(1);
  });
});
