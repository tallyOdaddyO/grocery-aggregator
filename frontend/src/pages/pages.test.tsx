/**
 * Page-level tests.
 *
 * The API module is mocked so partial failures, empty states, and error paths can
 * be driven deterministically - these are exactly the states that are hard to
 * reproduce against a healthy backend and most important to get right.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import basketJson from "../test/fixtures/basket.mixed.json";
import searchJson from "../test/fixtures/search.cheerios.json";
import type { CompareBasketResponse, SearchResponse } from "../types/api";
import { BasketPage } from "./BasketPage";
import { Dashboard } from "./Dashboard";
import { ProductPage } from "./ProductPage";
import { SearchPage } from "./SearchPage";

const api = vi.hoisted(() => ({
  fetchHealth: vi.fn(),
  searchProducts: vi.fn(),
  fetchProduct: vi.fn(),
  compareBasket: vi.fn(),
}));

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return { ...actual, ...api };
});

const { ApiError } = await import("../lib/api");

const search = searchJson as SearchResponse;
const basket = basketJson as CompareBasketResponse;

beforeEach(() => {
  api.fetchHealth.mockReset();
  api.searchProducts.mockReset();
  api.fetchProduct.mockReset();
  api.compareBasket.mockReset();
});
afterEach(() => vi.clearAllMocks());

function health(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    status: "ok",
    target_zip: "33009",
    source: "fixture",
    checked_at: new Date().toISOString(),
    connector_health: [
      { retailer: "walmart", status: "ok", latency_ms: 2, error_reason: null },
      {
        retailer: "rey_chavez",
        status: "degraded",
        latency_ms: 1,
        error_reason: "publishes no consumer prices",
      },
    ],
    ...overrides,
  };
}

// --------------------------------------------------------------- Dashboard
describe("Dashboard", () => {
  it("lists each retailer with its reachability", async () => {
    api.fetchHealth.mockResolvedValue(health());
    render(<MemoryRouter><Dashboard /></MemoryRouter>);

    const cards = await screen.findAllByTestId("retailer-card");
    expect(cards).toHaveLength(2);
    expect(cards[0]).toHaveTextContent("Walmart");
    expect(cards[1]).toHaveAttribute("data-status", "degraded");
    expect(cards[1]).toHaveTextContent("publishes no consumer prices");
  });

  it("says up front when the data is fixtures, not live prices", async () => {
    api.fetchHealth.mockResolvedValue(health());
    render(<MemoryRouter><Dashboard /></MemoryRouter>);
    expect(await screen.findByText(/not live retailer prices/i)).toBeInTheDocument();
  });

  it("reports a dead backend instead of rendering an empty page", async () => {
    api.fetchHealth.mockRejectedValue(
      new ApiError("Could not reach the RetailScout API. Is the backend running?", 0),
    );
    render(<MemoryRouter><Dashboard /></MemoryRouter>);
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not reach/i);
  });
});

// -------------------------------------------------------------- SearchPage
describe("SearchPage", () => {
  it("renders one card per match group with real API data", async () => {
    api.searchProducts.mockResolvedValue(search);
    render(
      <MemoryRouter initialEntries={["/search?q=cheerios"]}>
        <SearchPage />
      </MemoryRouter>,
    );
    const groups = await screen.findAllByTestId("match-group");
    expect(groups).toHaveLength(search.results.length);
  });

  it("shows freshness and both prices on every result", async () => {
    api.searchProducts.mockResolvedValue(search);
    render(
      <MemoryRouter initialEntries={["/search?q=cheerios"]}>
        <SearchPage />
      </MemoryRouter>,
    );
    const items = await screen.findAllByTestId("group-item");
    const first = within(items[0]!);
    expect(first.getByTestId("sticker-price")).toBeInTheDocument();
    expect(first.getByTestId("freshness")).toBeInTheDocument();
    expect(first.getByTestId("provenance")).toBeInTheDocument();
  });

  it("explains how each group was matched", async () => {
    api.searchProducts.mockResolvedValue(search);
    render(
      <MemoryRouter initialEntries={["/search?q=cheerios"]}>
        <SearchPage />
      </MemoryRouter>,
    );
    const labels = await screen.findAllByTestId("group-match-type");
    expect(labels[0]).toHaveTextContent(/matched by|only one retailer/i);
  });

  it("warns when a connector failed, without hiding the results", async () => {
    api.searchProducts.mockResolvedValue({
      ...search,
      is_complete: false,
      connector_health: [
        ...search.connector_health.slice(1),
        {
          retailer: "walmart",
          status: "degraded",
          latency_ms: 306,
          error_reason: "Walmart did not respond within 0.3s.",
        },
      ],
    });
    render(
      <MemoryRouter initialEntries={["/search?q=cheerios"]}>
        <SearchPage />
      </MemoryRouter>,
    );
    const banner = await screen.findByTestId("health-banner");
    expect(banner).toHaveTextContent(/incomplete/i);
    expect(banner).toHaveTextContent(/Walmart did not respond/);
    expect(screen.getAllByTestId("match-group").length).toBeGreaterThan(0);
  });

  it("shows no banner when every retailer reported", async () => {
    api.searchProducts.mockResolvedValue({ ...search, is_complete: true });
    render(
      <MemoryRouter initialEntries={["/search?q=cheerios"]}>
        <SearchPage />
      </MemoryRouter>,
    );
    await screen.findAllByTestId("match-group");
    expect(screen.queryByTestId("health-banner")).not.toBeInTheDocument();
  });

  it("states plainly when nothing matched", async () => {
    api.searchProducts.mockResolvedValue({
      ...search,
      results: [],
      query: "unobtainium",
    });
    render(
      <MemoryRouter initialEntries={["/search?q=unobtainium"]}>
        <SearchPage />
      </MemoryRouter>,
    );
    expect(await screen.findByTestId("no-results")).toHaveTextContent("unobtainium");
  });

  it("does not call the API before a query is entered", () => {
    render(<MemoryRouter initialEntries={["/search"]}><SearchPage /></MemoryRouter>);
    expect(api.searchProducts).not.toHaveBeenCalled();
  });

  it("searches on submit", async () => {
    api.searchProducts.mockResolvedValue({ ...search, results: [] });
    render(<MemoryRouter initialEntries={["/search"]}><SearchPage /></MemoryRouter>);
    await userEvent.type(screen.getByLabelText(/search groceries/i), "milk");
    await userEvent.click(screen.getByRole("button", { name: /search/i }));
    await waitFor(() => expect(api.searchProducts).toHaveBeenCalledWith(
      "milk", "33009", expect.anything(),
    ));
  });
});

// ------------------------------------------------------------- ProductPage
function productFixture(overrides: Record<string, unknown> = {}) {
  return {
    id: "publix:P-1002",
    retailer: "publix",
    title: "Cheerios Cereal, 12-oz box",
    brand: "General Mills",
    category: "cereal",
    upc: "00016000275270",
    size_raw: "12 oz",
    pack_count: 1,
    store: {
      retailer: "publix",
      store_number: "0479",
      name: "Publix Super Market",
      city: "Hallandale Beach",
      state: "FL",
      zip: "33009",
      address_verified: false,
    },
    current_price: {
      sticker_price_cents: 350,
      unit_price_cents: 467,
      unit_measure: "lb",
      provenance: {
        status: "verified_online",
        timestamp: new Date(Date.now() - 18 * 60_000).toISOString(),
        source_url: null,
        verification_method: "verified_online",
        is_fresh: true,
      },
    },
    price_history: [
      {
        observed_at: new Date(Date.now() - 18 * 60_000).toISOString(),
        sticker_price_cents: 350,
        unit_price_cents: 467,
        unit_measure: "lb",
        promotion_type: "sale",
        provenance: {
          status: "verified_online",
          timestamp: new Date(Date.now() - 18 * 60_000).toISOString(),
          source_url: null,
          verification_method: "verified_online",
          is_fresh: true,
        },
      },
      {
        observed_at: new Date(Date.now() - 3 * 86_400_000).toISOString(),
        sticker_price_cents: 399,
        unit_price_cents: 532,
        unit_measure: "lb",
        promotion_type: "none",
        provenance: {
          status: "verified_online",
          timestamp: new Date(Date.now() - 3 * 86_400_000).toISOString(),
          source_url: null,
          verification_method: "verified_online",
          is_fresh: false,
        },
      },
    ],
    confidence_stats: {
      match_confidence: 1,
      match_type: "upc",
      threshold: 0.82,
      veto_checks_passed: ["unit_dimension", "package_size", "brand"],
      veto_checks_failed: [],
      signals: [{ name: "upc", detail: "UPC exact match", weight: 1 }],
      explanation: "Confidence 100%: UPC exact match",
      equivalent_count: 4,
    },
    equivalent_products: [],
    ...overrides,
  };
}

function renderProduct(id = "publix:P-1002") {
  return render(
    <MemoryRouter initialEntries={[`/product/${encodeURIComponent(id)}`]}>
      <Routes>
        <Route path="/product/:id" element={<ProductPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("ProductPage", () => {
  it("shows sticker and unit price distinctly, plus freshness", async () => {
    api.fetchProduct.mockResolvedValue(productFixture());
    renderProduct();
    await screen.findByRole("heading", { name: /cheerios/i });
    expect(screen.getAllByTestId("sticker-price")[0]).toHaveTextContent("$3.50");
    expect(screen.getAllByTestId("unit-price")[0]).toHaveTextContent("$4.67/lb");
    expect(screen.getAllByTestId("freshness")[0]).toHaveTextContent(
      "Checked 18 minutes ago",
    );
  });

  it("renders the price history newest first", async () => {
    api.fetchProduct.mockResolvedValue(productFixture());
    renderProduct();
    const rows = within(await screen.findByTestId("history-table")).getAllByRole("row");
    // row[0] is the header.
    expect(rows[1]).toHaveTextContent("$3.50");
    expect(rows[2]).toHaveTextContent("$3.99");
  });

  it("explains the match with the veto checks that passed", async () => {
    api.fetchProduct.mockResolvedValue(productFixture());
    renderProduct();
    const explanation = await screen.findByTestId("match-explanation");
    expect(explanation).toHaveTextContent("Package size");
    expect(explanation).toHaveTextContent("Brand");
  });

  it("marks an unverified store address as unverified", async () => {
    api.fetchProduct.mockResolvedValue(productFixture());
    renderProduct();
    expect(await screen.findByTestId("store")).toHaveTextContent(/address unverified/i);
  });

  it("handles a product with no observed price history", async () => {
    api.fetchProduct.mockResolvedValue(productFixture({ price_history: [] }));
    renderProduct();
    expect(await screen.findByTestId("no-history")).toBeInTheDocument();
  });

  it("handles an unpriced but stocked product", async () => {
    api.fetchProduct.mockResolvedValue(
      productFixture({
        price_history: [],
        current_price: {
          sticker_price_cents: null,
          unit_price_cents: null,
          unit_measure: "unknown",
          provenance: {
            status: "no_price_published",
            timestamp: new Date().toISOString(),
            source_url: null,
            verification_method: "no_price_published",
            is_fresh: false,
          },
        },
      }),
    );
    renderProduct();
    expect(await screen.findByText("Price on request")).toBeInTheDocument();
    expect(screen.queryByText("$0.00")).not.toBeInTheDocument();
  });

  it("says so when there is no equivalent elsewhere", async () => {
    api.fetchProduct.mockResolvedValue(productFixture());
    renderProduct();
    expect(await screen.findByTestId("no-equivalents")).toBeInTheDocument();
  });

  it("shows a friendly message for an unknown id", async () => {
    api.fetchProduct.mockRejectedValue(new ApiError("No product found with id 'x'.", 404));
    renderProduct("nope");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /don’t have a product with that id/i,
    );
  });
});

// -------------------------------------------------------------- BasketPage
describe("BasketPage", () => {
  it("compares a basket and renders both plans", async () => {
    api.compareBasket.mockResolvedValue(basket);
    render(<MemoryRouter><BasketPage /></MemoryRouter>);

    await userEvent.type(screen.getByLabelText("Item 1"), "milk");
    await userEvent.click(screen.getByRole("button", { name: /compare/i }));

    expect(await screen.findByTestId("plan-split")).toBeInTheDocument();
    expect(screen.getByTestId("plan-complete")).toBeInTheDocument();
    expect(api.compareBasket).toHaveBeenCalledWith(
      [{ query: "milk", quantity: 1 }], "33009",
    );
  });

  it("cannot be submitted empty", async () => {
    render(<MemoryRouter><BasketPage /></MemoryRouter>);
    expect(screen.getByRole("button", { name: /compare/i })).toBeDisabled();
    expect(api.compareBasket).not.toHaveBeenCalled();
  });

  it("adds further lines", async () => {
    render(<MemoryRouter><BasketPage /></MemoryRouter>);
    await userEvent.click(screen.getByRole("button", { name: /add item/i }));
    expect(screen.getByLabelText("Item 2")).toBeInTheDocument();
  });

  it("reports items nothing nearby can supply", async () => {
    api.compareBasket.mockResolvedValue(basket);
    render(<MemoryRouter><BasketPage /></MemoryRouter>);
    await userEvent.type(screen.getByLabelText("Item 1"), "unobtainium");
    await userEvent.click(screen.getByRole("button", { name: /compare/i }));
    expect(await screen.findByTestId("unavailable")).toHaveTextContent("unobtainium");
  });

  it("warns when the comparison is incomplete", async () => {
    api.compareBasket.mockResolvedValue({
      ...basket,
      is_complete: false,
      connector_health: [
        {
          retailer: "walmart",
          status: "degraded",
          latency_ms: 306,
          error_reason: "Walmart did not respond within 0.3s.",
        },
      ],
    });
    render(<MemoryRouter><BasketPage /></MemoryRouter>);
    await userEvent.type(screen.getByLabelText("Item 1"), "milk");
    await userEvent.click(screen.getByRole("button", { name: /compare/i }));
    expect(await screen.findByTestId("health-banner")).toHaveTextContent(/incomplete/i);
  });

  it("surfaces a backend failure instead of silently doing nothing", async () => {
    api.compareBasket.mockRejectedValue(new ApiError("Comparison exploded", 500));
    render(<MemoryRouter><BasketPage /></MemoryRouter>);
    await userEvent.type(screen.getByLabelText("Item 1"), "milk");
    await userEvent.click(screen.getByRole("button", { name: /compare/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Comparison exploded");
  });
});
