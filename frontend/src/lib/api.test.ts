import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, compareBasket, fetchHealth, fetchProduct, searchProducts } from "./api";

function mockFetch(body: unknown, init: { status?: number } = {}) {
  const status = init.status ?? 200;
  const spy = vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response);
  vi.stubGlobal("fetch", spy);
  return spy;
}

afterEach(() => vi.unstubAllGlobals());

describe("searchProducts", () => {
  it("calls the v1 endpoint with the query and zip", async () => {
    const spy = mockFetch({ query: "milk", results: [] });
    await searchProducts("milk", "33009");
    const url = new URL(spy.mock.calls[0]![0] as string, "http://localhost");
    expect(url.pathname).toBe("/api/v1/search");
    expect(url.searchParams.get("q")).toBe("milk");
    expect(url.searchParams.get("zip")).toBe("33009");
  });

  it("encodes queries containing spaces and symbols", async () => {
    const spy = mockFetch({ results: [] });
    await searchProducts("peanut butter & jam", "33009");
    const url = new URL(spy.mock.calls[0]![0] as string, "http://localhost");
    expect(url.searchParams.get("q")).toBe("peanut butter & jam");
  });
});

describe("fetchProduct", () => {
  it("encodes the composite id so the colon survives", async () => {
    const spy = mockFetch({ id: "publix:P-1002" });
    await fetchProduct("publix:P-1002");
    expect(spy.mock.calls[0]![0]).toContain("publix%3AP-1002");
  });

  it("raises a typed 404 the UI can distinguish", async () => {
    mockFetch({ detail: "No product found with id 'nope'." }, { status: 404 });
    await expect(fetchProduct("nope")).rejects.toBeInstanceOf(ApiError);
    await expect(fetchProduct("nope")).rejects.toMatchObject({
      status: 404,
      message: "No product found with id 'nope'.",
    });
  });
});

describe("compareBasket", () => {
  it("posts the basket as JSON", async () => {
    const spy = mockFetch({ cheapest_split: null });
    await compareBasket([{ query: "milk", quantity: 2 }], "33009");
    const [, init] = spy.mock.calls[0] as [string, RequestInit];
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({
      zip_code: "33009",
      items: [{ query: "milk", quantity: 2 }],
    });
  });
});

describe("error handling", () => {
  it("surfaces a server error rather than returning undefined", async () => {
    mockFetch({ detail: "boom" }, { status: 500 });
    await expect(searchProducts("milk", "33009")).rejects.toBeInstanceOf(ApiError);
  });

  it("survives a non-JSON error body", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 502,
        json: async () => {
          throw new SyntaxError("not json");
        },
      } as unknown as Response),
    );
    await expect(fetchHealth("33009")).rejects.toMatchObject({ status: 502 });
  });

  it("reports a network failure as an ApiError, not a raw TypeError", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(searchProducts("milk", "33009")).rejects.toBeInstanceOf(ApiError);
  });
});
