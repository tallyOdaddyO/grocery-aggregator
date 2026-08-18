import type { PriceData, VerificationMethod } from "../types/api";
import { formatCents, formatCheckedAt, formatUnitPrice } from "../lib/format";

/**
 * How each provenance grade is described to a shopper.
 *
 * The wording is deliberately unflattering where it should be: a retailer's own
 * published price is not a shelf check, and saying so is the whole point of
 * carrying provenance through the stack.
 */
const PROVENANCE_LABEL: Record<VerificationMethod, string> = {
  verified_in_store: "Verified in store",
  verified_online: "Retailer price, not shelf-checked",
  delivery_price: "Delivery price, may differ in store",
  estimated: "Estimated from a circular",
  no_price_published: "No published price",
};

const PROVENANCE_TONE: Record<VerificationMethod, string> = {
  verified_in_store: "tone-strong",
  verified_online: "tone-good",
  delivery_price: "tone-caution",
  estimated: "tone-caution",
  no_price_published: "tone-muted",
};

export function PriceTag({ price, now }: { price: PriceData; now?: Date }) {
  const { provenance } = price;
  const unitPrice = formatUnitPrice(price.unit_price_cents, price.unit_measure);
  const hasPrice = price.sticker_price_cents !== null;

  return (
    <div className="price-tag">
      {hasPrice ? (
        <>
          <span className="price-sticker" data-testid="sticker-price">
            {formatCents(price.sticker_price_cents)}
          </span>
          {/* Kept visually and semantically separate: the unit price is a
              comparison aid, not the amount charged at the register. */}
          {unitPrice && (
            <span className="price-unit" data-testid="unit-price">
              {unitPrice}
            </span>
          )}
        </>
      ) : (
        // Carried by this retailer, but the price is not published (a
        // distributor quoting on request, for example). Deliberately not "$0.00".
        <span className="price-none" data-testid="sticker-price">
          Price on request
        </span>
      )}

      <span
        className={`badge ${PROVENANCE_TONE[provenance.verification_method]}`}
        data-testid="provenance"
      >
        {PROVENANCE_LABEL[provenance.verification_method]}
      </span>

      {hasPrice && (
        <span
          className="freshness"
          data-testid="freshness"
          data-fresh={String(provenance.is_fresh)}
          title={provenance.timestamp}
        >
          {formatCheckedAt(provenance.timestamp, now)}
          {!provenance.is_fresh && " — may be out of date"}
        </span>
      )}
    </div>
  );
}
