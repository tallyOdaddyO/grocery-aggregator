import { Link } from "react-router-dom";
import type { MatchGroup } from "../types/api";
import { retailerName } from "../lib/retailers";
import { PriceTag } from "./PriceTag";

const MATCH_LABEL: Record<string, string> = {
  upc: "Matched by barcode",
  attributes: "Matched on product attributes",
  fuzzy: "Matched on name and size",
  singleton: "Only one retailer nearby carries this",
};

/**
 * One equivalence group: the same product priced at several stores.
 *
 * Items are ordered by unit price where known, because that is the comparison
 * the group exists to support - but the sticker price stays visible so a good
 * unit price on a huge package cannot masquerade as a cheap item.
 */
export function MatchGroupCard({ group, now }: { group: MatchGroup; now?: Date }) {
  const items = [...group.items].sort((a, b) => {
    const left = a.price.unit_price_cents;
    const right = b.price.unit_price_cents;
    if (left === null && right === null) return 0;
    if (left === null) return 1;
    if (right === null) return -1;
    return left - right;
  });

  return (
    <article className="group-card" data-testid="match-group">
      <header>
        <h3>{group.canonical_name}</h3>
        <p className="group-match" data-testid="group-match-type">
          {MATCH_LABEL[group.match_type] ?? group.match_type}
        </p>
      </header>
      <ul className="group-items">
        {items.map((item) => (
          <li key={item.id} className="group-item" data-testid="group-item">
            <Link to={`/product/${encodeURIComponent(item.id)}`} className="item-link">
              {item.title}
            </Link>
            <span className="item-retailer">{retailerName(item.retailer)}</span>
            <span className="item-size">{item.size_raw}</span>
            <PriceTag price={item.price} now={now} />
          </li>
        ))}
      </ul>
    </article>
  );
}
