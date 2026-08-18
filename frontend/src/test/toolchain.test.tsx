import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { App } from "../App";

vi.mock("../lib/api", () => ({
  ApiError: class extends Error {
    status = 0;
  },
  fetchHealth: vi.fn().mockResolvedValue({
    status: "ok",
    target_zip: "33009",
    source: "fixture",
    checked_at: new Date().toISOString(),
    connector_health: [],
  }),
  searchProducts: vi.fn(),
  fetchProduct: vi.fn(),
  compareBasket: vi.fn(),
}));

describe("App shell", () => {
  // The dashboard fetches on mount. Each test awaits that settling, otherwise the
  // resolution lands after the test ends and React reports an unwrapped update -
  // which would also mean a real state-after-unmount leak in the component.
  it("renders the header and primary navigation", async () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <App />
      </MemoryRouter>,
    );
    expect(await screen.findByText(/data source/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "RetailScout" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Search" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Basket" })).toBeInTheDocument();
  });

  it("shows which ZIP is being served", async () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <App />
      </MemoryRouter>,
    );
    expect(await screen.findByText(/data source/i)).toBeInTheDocument();
    expect(screen.getByText("ZIP 33009")).toBeInTheDocument();
  });
});
