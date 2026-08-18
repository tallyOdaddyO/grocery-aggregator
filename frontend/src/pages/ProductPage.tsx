import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, fetchProduct } from "../lib/api";
import { MatchExplanation } from "../components/MatchExplanation";
import { PriceTag } from "./../components/PriceTag";
import { formatCents, formatCheckedAt, formatUnitPrice } from "../lib/format";
import { NOTE_LABEL, retailerName } from "../lib/retailers";
import type { ProductDetailResponse } from "../types/api";

export function ProductPage({ now }: { now?: Date }) {
  const { id = "" } = useParams();
  const [data, setData] = useState<ProductDetailResponse | null>(null);
  const [error, setError] = useState<{ message: string; status: number } | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    fetchProduct(id, controller.signal)
      .then((response) => setData(response))
      .catch((cause) => {
        if (controller.signal.aborted) return;
        setData(null);
        setError(
          cause instanceof ApiError
            ? { message: cause.message, status: cause.status }
            : { message: "Could not load this product.", status: 0 },
        );
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [id]);

  if (loading) return <p role="status">Loading product…</p>;

  if (error) {
    return (
      <section className="product-page">
        <p role="alert" className="error">
          {error.status === 404 ? "We don’t have a product with that id." : error.message}
        </p>
        <Link to="/search">Back to search</Link>
      </section>
    );
  }

  if (!data) return null;

  return (
    <section className="product-page">
      <header>
        <h2>{data.title}</h2>
        <p className="product-meta">
          {data.brand && <span>{data.brand}</span>}
          <span>{retailerName(data.retailer)}</span>
          <span>{data.size_raw}</span>
          {data.upc ? (
            <span className="upc">UPC {data.upc}</span>
          ) : (
            <span className="upc-missing">No barcode published</span>
          )}
        </p>
        <p className="product-store" data-testid="store">
          {data.store.name ?? retailerName(data.retailer)} #{data.store.store_number},{" "}
          {data.store.city ?? "location unknown"} {data.store.zip}
          {/* Never presented as a confirmed address unless it actually is. */}
          {!data.store.address_verified && (
            <span className="badge tone-muted"> address unverified</span>
          )}
        </p>
      </header>

      <div className="product-price">
        <h3>Current price</h3>
        <PriceTag price={data.current_price} now={now} />
      </div>

      <div className="product-confidence">
        <h3>Why we matched this</h3>
        <MatchExplanation stats={data.confidence_stats} />
      </div>

      <div className="product-history">
        <h3>Price history</h3>
        {data.price_history.length === 0 ? (
          <p className="empty" data-testid="no-history">
            No price has been observed for this item yet.
          </p>
        ) : (
          <table data-testid="history-table">
            <thead>
              <tr>
                <th>Observed</th>
                <th>Price</th>
                <th>Unit price</th>
                <th>Source</th>
              </tr>
            </thead>
            <tbody>
              {data.price_history.map((entry) => (
                <tr key={`${entry.observed_at}-${entry.sticker_price_cents}`}>
                  <td title={entry.observed_at}>
                    {formatCheckedAt(entry.observed_at, now)}
                  </td>
                  <td>{formatCents(entry.sticker_price_cents)}</td>
                  <td>
                    {formatUnitPrice(entry.unit_price_cents, entry.unit_measure) ?? "—"}
                  </td>
                  <td>{entry.provenance.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="product-equivalents">
        <h3>Same product elsewhere</h3>
        {data.equivalent_products.length === 0 ? (
          <p className="empty" data-testid="no-equivalents">
            No equivalent product was found at another retailer nearby.
          </p>
        ) : (
          <ul>
            {data.equivalent_products.map((item) => (
              <li key={item.id} data-testid="equivalent">
                <Link to={`/product/${encodeURIComponent(item.id)}`}>{item.title}</Link>
                <span className="item-retailer">{retailerName(item.retailer)}</span>
                <PriceTag price={item.price} now={now} />
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

export const PRODUCT_NOTE_LABEL = NOTE_LABEL;
