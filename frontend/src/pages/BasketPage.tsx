import { useState } from "react";
import { ApiError, compareBasket } from "../lib/api";
import { BasketComparison } from "../components/BasketComparison";
import { ConnectorHealthBanner } from "../components/ConnectorHealthBanner";
import type { CompareBasketResponse } from "../types/api";

interface Line {
  query: string;
  quantity: number;
}

const STARTER: Line[] = [{ query: "", quantity: 1 }];

export function BasketPage({ zip = "33009", now }: { zip?: string; now?: Date }) {
  const [lines, setLines] = useState<Line[]>(STARTER);
  const [data, setData] = useState<CompareBasketResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const filled = lines.filter((line) => line.query.trim().length > 0);

  function update(index: number, patch: Partial<Line>) {
    setLines((current) =>
      current.map((line, i) => (i === index ? { ...line, ...patch } : line)),
    );
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (filled.length === 0) return;
    setLoading(true);
    setError(null);
    try {
      setData(await compareBasket(filled, zip));
    } catch (cause) {
      setData(null);
      setError(cause instanceof ApiError ? cause.message : "Comparison failed.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="basket-page">
      <h2>Compare a basket</h2>

      <form onSubmit={submit} className="basket-form">
        <ul className="basket-lines">
          {lines.map((line, index) => (
            <li key={index} className="basket-line">
              <label htmlFor={`item-${index}`}>Item {index + 1}</label>
              <input
                id={`item-${index}`}
                value={line.query}
                placeholder="e.g. milk"
                onChange={(event) => update(index, { query: event.target.value })}
              />
              <label htmlFor={`qty-${index}`}>Quantity</label>
              <input
                id={`qty-${index}`}
                type="number"
                min={1}
                max={99}
                value={line.quantity}
                onChange={(event) =>
                  update(index, { quantity: Math.max(1, Number(event.target.value) || 1) })
                }
              />
            </li>
          ))}
        </ul>
        <button
          type="button"
          onClick={() => setLines((current) => [...current, { query: "", quantity: 1 }])}
        >
          Add item
        </button>
        <button type="submit" disabled={filled.length === 0 || loading}>
          Compare
        </button>
      </form>

      {loading && <p role="status">Comparing across every retailer…</p>}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}

      {data && (
        <>
          <ConnectorHealthBanner
            health={data.connector_health}
            isComplete={data.is_complete}
          />
          <BasketComparison data={data} now={now} />
        </>
      )}
    </section>
  );
}
