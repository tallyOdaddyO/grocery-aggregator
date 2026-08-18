/**
 * Types mirroring the FastAPI v1 wire schemas.
 *
 * `status` carries the exact internal grade and is finer-grained than
 * `verification_method`; the UI prefers `verification_method` for its label and
 * falls back to `status` only for grades the enum does not carry (e.g. `stale`).
 */

export type RetailerID =
  | "walmart"
  | "costco"
  | "bjs"
  | "publix"
  | "winn_dixie"
  | "fresco_y_mas"
  | "presidente"
  | "rey_chavez";

export type VerificationMethod =
  | "verified_in_store"
  | "verified_online"
  | "delivery_price"
  | "estimated"
  | "no_price_published";

export type ConnectorStatus = "ok" | "degraded" | "unavailable";

export interface PriceProvenance {
  status: string;
  timestamp: string;
  source_url: string | null;
  verification_method: VerificationMethod;
  is_fresh: boolean;
}

export interface PriceData {
  sticker_price_cents: number | null;
  unit_price_cents: number | null;
  unit_measure: string;
  provenance: PriceProvenance;
}

export interface ConnectorHealth {
  retailer: RetailerID;
  status: ConnectorStatus;
  latency_ms: number;
  error_reason: string | null;
}

export interface SearchProductSummary {
  id: string;
  retailer: RetailerID;
  title: string;
  size_raw: string;
  price: PriceData;
}

export interface MatchGroup {
  group_id: string;
  canonical_name: string;
  match_type: string;
  items: SearchProductSummary[];
}

export interface SearchResponse {
  query: string;
  zip_code: string;
  is_complete: boolean;
  connector_health: ConnectorHealth[];
  results: MatchGroup[];
}

export interface MatchSignal {
  name: string;
  detail: string;
  weight: number;
}

export interface ConfidenceStats {
  match_confidence: number;
  match_type: string;
  threshold: number;
  veto_checks_passed: string[];
  veto_checks_failed: string[];
  signals: MatchSignal[];
  explanation: string;
  equivalent_count: number;
}

export interface BasketLineItem {
  query: string;
  quantity: number;
  product_id: string;
  retailer: RetailerID;
  title: string;
  size_raw: string;
  price: PriceData;
  line_total_cents: number;
  notes: string[];
}

export interface RetailerTrip {
  retailer: RetailerID;
  store_number: string;
  items: BasketLineItem[];
  subtotal_cents: number;
}

export interface BasketPlan {
  strategy: string;
  trips: RetailerTrip[];
  total_cents: number;
  item_count: number;
  stop_count: number;
}

export interface UnavailableItem {
  query: string;
  quantity: number;
  reason: string;
}

export interface CompareBasketResponse {
  zip_code: string;
  is_complete: boolean;
  connector_health: ConnectorHealth[];
  cheapest_complete: BasketPlan | null;
  cheapest_split: BasketPlan | null;
  savings_cents: number | null;
  unavailable_items: UnavailableItem[];
}
