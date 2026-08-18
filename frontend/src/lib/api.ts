/**
 * Typed client for the RetailScout v1 API.
 *
 * Every failure becomes an `ApiError` carrying the HTTP status, so callers can
 * distinguish "this product does not exist" (404) from "the backend is down"
 * (network / 5xx) and say something truthful in each case.
 */
import type {
  CompareBasketResponse,
  ConnectorHealth,
  ProductDetailResponse,
  SearchResponse,
} from "../types/api";

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** 0 means the request never reached the server. */
export const NETWORK_ERROR_STATUS = 0;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch (cause) {
    throw new ApiError(
      "Could not reach the RetailScout API. Is the backend running?",
      NETWORK_ERROR_STATUS,
    );
  }

  if (!response.ok) {
    let detail = `Request failed with status ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body?.detail) detail = body.detail;
    } catch {
      // A non-JSON error body is still an error; keep the status-based message.
    }
    throw new ApiError(detail, response.status);
  }

  return (await response.json()) as T;
}

export function searchProducts(
  query: string,
  zip: string,
  signal?: AbortSignal,
): Promise<SearchResponse> {
  const params = new URLSearchParams({ q: query, zip });
  return request<SearchResponse>(`/api/v1/search?${params}`, { signal });
}

export function fetchProduct(
  id: string,
  signal?: AbortSignal,
): Promise<ProductDetailResponse> {
  // The id is a composite "retailer:sku"; the colon must survive the path.
  return request<ProductDetailResponse>(
    `/api/v1/product/${encodeURIComponent(id)}`,
    { signal },
  );
}

export function compareBasket(
  items: { query: string; quantity: number }[],
  zip: string,
  signal?: AbortSignal,
): Promise<CompareBasketResponse> {
  return request<CompareBasketResponse>("/api/v1/compare-basket", {
    method: "POST",
    body: JSON.stringify({ zip_code: zip, items }),
    signal,
  });
}

export interface HealthResponse {
  status: string;
  target_zip: string;
  source: string;
  checked_at: string;
  connector_health: ConnectorHealth[];
}

export function fetchHealth(zip: string, signal?: AbortSignal): Promise<HealthResponse> {
  const params = new URLSearchParams({ zip });
  return request<HealthResponse>(`/api/v1/health?${params}`, { signal });
}
