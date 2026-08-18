# RetailScout — Architecture

Local Grocery Price Aggregator for ZIP **33009** (Hallandale Beach, FL).

Search a grocery item → find matching products across local retailers → group them
(Product → Brand → Store → Price) → compare on normalized units → emit per-store
shopping lists.

---

## 1. Guiding Principles

1. **Fixture-first.** Every connector is developed and tested against local fixtures
   in `/fixtures`. Live endpoints are wired only in Phase 7, and only where permitted.
   We do not attempt to defeat CAPTCHAs, WAFs, or bot protection.
2. **Never fabricate a price.** A retailer that cannot produce usable data is marked
   `degraded` or `unavailable`. Every price carries an explicit provenance grade.
3. **Location is first-class.** Online prices ≠ local shelf prices. An adapter must
   resolve its local store/warehouse for the ZIP *before* it may fetch pricing.
4. **Explainable matching.** Every product match carries a confidence score and a
   human-readable list of the signals that produced it.
5. **Degrade partially, never totally.** One failing connector must not fail a search.

---

## 2. Component Map

```
                     ┌────────────────────────┐
  Browser  ────────► │  frontend/  React+TS   │
                     └───────────┬────────────┘
                                 │  REST /api/v1
                     ┌───────────▼────────────┐
                     │  backend/  FastAPI     │
                     │  ├ api/v1  routers     │
                     │  ├ services            │
                     │  │   ├ normalization   │  units, pack sizes
                     │  │   ├ matching        │  3-stage engine
                     │  │   ├ search          │  fan-out + grouping
                     │  │   └ basket          │  single vs split optimizer
                     │  └ connectors          │  8 retailer adapters
                     └─────┬─────────────┬────┘
                           │             │
              ┌────────────▼───┐   ┌─────▼──────────────┐
              │ PostgreSQL     │   │ Redis + ARQ        │
              │ (SQLite in dev)│   │ workers/ refresh   │
              └────────────────┘   └─────┬──────────────┘
                                         │
                              ┌──────────▼───────────┐
                              │ fixtures/ | live web │
                              └──────────────────────┘
```

### Directory layout

| Path | Contents |
|---|---|
| `backend/app/api/v1/` | FastAPI routers (`search`, `product`, `basket`, `health`) |
| `backend/app/core/` | settings, logging, enums, exceptions |
| `backend/app/db/` | engine, session, base metadata |
| `backend/app/models/` | SQLAlchemy ORM models |
| `backend/app/schemas/` | Pydantic request/response models |
| `backend/app/services/` | normalization, matching, search, basket |
| `backend/app/connectors/` | `BaseRetailerConnector` + 8 adapters |
| `backend/alembic/` | migrations |
| `backend/tests/` | pytest suite |
| `workers/` | ARQ job definitions, schedules, backoff policy |
| `frontend/` | Vite + React + TypeScript client |
| `fixtures/<retailer>/` | captured payloads, one dir per retailer |
| `scripts/` | seed + maintenance CLI scripts |

---

## 3. Data Model

Normalized, append-only where it matters. Current prices live in `prices`;
every observation ever made is retained in `price_observations`.

```
retailers ──< stores ──< prices >── product_variants >── products
                             │
                             └──< price_observations   (append-only log)

shopping_lists ──< shopping_list_items >── product_variants
```

| Table | Purpose | Key columns |
|---|---|---|
| `retailers` | One row per chain | `slug`, `name`, `status`, `last_sync_at`, `supports_online_pricing` |
| `stores` | Physical location of a chain | `retailer_id`, `store_number`, `address`, `city`, `state`, `zip`, `lat`, `lon`, `is_primary_for_zip` |
| `products` | Canonical product identity | `upc`, `normalized_name`, `brand`, `category` |
| `product_variants` | A specific sellable package | `product_id`, `retailer_sku`, `pack_count`, `net_content_value`, `net_content_uom`, `is_organic` |
| `prices` | Current price of a variant at a store | `variant_id`, `store_id`, `price_cents`, `unit_price_cents`, `unit_price_uom`, `promotion_type`, `provenance`, `observed_at` |
| `price_observations` | Append-only history | same shape as `prices`, never updated |
| `shopping_lists` / `shopping_list_items` | Saved baskets | `zip`, `strategy`, `quantity` |
| `product_matches` | Materialized match edges | `left_variant_id`, `right_variant_id`, `confidence`, `signals` (JSON), `stage` |

**Portability note.** Models target PostgreSQL. The dev/test default is SQLite so the
suite runs without a server; JSON columns use `JSON` (not `JSONB`) and money is stored
as integer cents to keep both backends exact.

### Price provenance (`PriceProvenance` enum)

Ordered most → least trustworthy. The UI must render this verbatim; it is never inferred.

| Value | Meaning |
|---|---|
| `verified_in_store` | Confirmed against the shelf/register at a specific store |
| `verified_online` | Retailer site/API, resolved to the local store |
| `delivery_price` | Third-party or delivery-marked-up price; not shelf price |
| `estimated` | Derived (e.g. regional average). Never presented as fact |
| `stale` | Was valid, now older than the category's freshness TTL |

`unavailable` is a *retailer* status, not a price grade — no row is written at all.

---

## 4. Product Matching Engine

Three stages, short-circuiting. Each contributing signal is recorded so the result
can explain itself.

**Stage 1 — Identity.** UPC/GTIN-14 normalized (check digit validated, leading zeros
stripped). Exact match ⇒ confidence 1.0, done.

**Stage 2 — Normalized attributes.** Compare brand, category, variant flags
(organic / low-fat / flavor), pack count, and net content converted to a common base
unit. Size equivalence uses a tolerance (default 2%) so "16 oz" and "1 lb" agree.
A hard mismatch on size or organic flag is a **veto** — it cannot be outvoted by
name similarity.

**Stage 3 — Fuzzy.** Token-set ratio + Levenshtein over normalized names, with
stopword and pack-descriptor stripping. Thresholds are per-category and configurable
(produce is looser, pharmacy/baby formula is strict).

**Output.** `MatchResult { confidence, stage, signals[], vetoed }` where a signal is
e.g. `("upc", "exact", 1.0)` or `("size", "equivalent 16oz≈1lb", 0.25)`, rendering as
*"Confidence 97%: UPC exact, size equivalent."*

---

## 5. Unit Normalization

Parse a free-text package descriptor into a structured quantity, then project to a
comparison basis:

- mass → grams (report `$/lb`, `$/100g`)
- volume → milliliters (report `$/fl oz`, `$/L`)
- count → each (report `$/count`)

**Sticker price vs unit price** are always kept separate. Costco's $19.99 40-ct box
and Publix's $6.49 12-ct box are only comparable through `$/count`; the UI shows both
numbers side by side so bulk quantity is never hidden behind a good unit price.

---

## 6. Connector Contract

```python
class BaseRetailerConnector(ABC):
    slug: str
    supports_online_pricing: bool

    def resolve_store(self, zip_code: str) -> StoreRef | None: ...
    def search(self, term: str, store: StoreRef) -> list[RawProduct]: ...
    def fetch_variant(self, sku: str, store: StoreRef) -> RawProduct | None: ...
    def health(self) -> ConnectorHealth: ...
```

- A connector that cannot `resolve_store` for the ZIP returns `unavailable` and yields
  zero products; it never falls back to a national price.
- Every adapter runs against `fixtures/<slug>/` when `RETAILSCOUT_SOURCE=fixture`.
- The search fan-out isolates each connector: exceptions and timeouts are captured
  per-retailer and surfaced in the response as `degraded_retailers[]` while the rest
  of the results return normally.

### Retailer outlook (to be confirmed empirically in Phase 7)

| Retailer | Expected posture |
|---|---|
| Walmart | Heavy bot protection. Fixture-only unless a permitted API is available |
| Costco | Warehouse-scoped, membership-gated. Likely fixture / manual capture |
| BJ's | Club-scoped, likely protected |
| Publix | Store-scoped weekly ad is the most promising signal |
| Winn-Dixie | Store-scoped digital circular |
| Fresco y Más | Same platform family as Winn-Dixie |
| Presidente | Small chain; circular is likely PDF/image → may stay `degraded` |
| Rey Chavez | Distributor, not retail; pricing may be unavailable entirely |

No retailer is assumed to work. Each is proven, or marked.

---

## 7. API Surface (v1)

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/search?q=&zip=` | Normalized products grouped by match, with per-group price spread, freshness, and degraded-retailer list |
| `GET /api/v1/product/{id}` | Detail, variants, price history, match confidence stats |
| `POST /api/v1/compare-basket` | Cheapest single-store basket vs cheapest split basket, incl. items no store can supply |
| `GET /api/v1/health` | Service + per-connector status |

Every price-bearing response includes `observed_at` and `provenance` so the client can
render "Checked 18 minutes ago" honestly.

---

## 8. Workers

Redis + ARQ. Jobs: `refresh_store_prices`, `refresh_variant`, `reconcile_matches`.
Exponential backoff with jitter, per-retailer concurrency caps and rate limits,
and a circuit breaker that flips a retailer to `degraded` after N consecutive failures.
Scheduled sweeps are staggered per retailer; a price older than its category TTL is
demoted to `stale` rather than deleted.

---

## 9. Testing Strategy

- **Unit:** normalization parsers and unit conversion (heavy table-driven cases);
  matching engine including negative cases (12oz vs 24oz must *not* match).
- **Connector:** each adapter parses its fixtures into valid `RawProduct`s; malformed
  fixtures must raise cleanly, not silently yield partial rows.
- **Pipeline:** a search where one connector raises still returns the other seven.
- **API:** FastAPI TestClient against a seeded SQLite database.

Phase gate: the suite must pass before the next phase begins.
