import type { RetailerID } from "../types/api";

/** Display names for the eight target retailers. */
export const RETAILER_NAME: Record<RetailerID, string> = {
  walmart: "Walmart",
  costco: "Costco Wholesale",
  bjs: "BJ's Wholesale Club",
  publix: "Publix",
  winn_dixie: "Winn-Dixie",
  fresco_y_mas: "Fresco y Más",
  presidente: "Presidente Supermarket",
  rey_chavez: "Rey Chavez Distributors",
};

/** Caveats the adapters attach to a line, in words a shopper can act on. */
export const NOTE_LABEL: Record<string, string> = {
  multi_buy_required:
    "Price requires buying more than one — check the deal quantity",
  membership_required: "Membership required",
  price_from_circular: "Price taken from a weekly circular",
  ambiguous_oz: "Package size units were ambiguous",
  size_unparsed: "Package size could not be read, so there is no unit price",
  upc_missing: "No barcode published for this item",
  upc_invalid: "The published barcode failed its check digit",
};

export function retailerName(id: RetailerID): string {
  return RETAILER_NAME[id] ?? id;
}
