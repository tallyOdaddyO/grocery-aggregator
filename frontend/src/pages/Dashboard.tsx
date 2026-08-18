import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, fetchHealth, type HealthResponse } from "../lib/api";
import { retailerName } from "../lib/retailers";
import { formatCheckedAt } from "../lib/format";

const STATUS_TEXT: Record<string, string> = {
  ok: "Reachable",
  degraded: "Degraded",
  unavailable: "Unavailable",
};

/** Landing view: which retailers can actually be reached for this ZIP. */
export function Dashboard({ zip = "33009", now }: { zip?: string; now?: Date }) {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    fetchHealth(zip, controller.signal)
      .then((data) => {
        setHealth(data);
        setError(null);
      })
      .catch((cause) => {
        if (controller.signal.aborted) return;
        setError(cause instanceof ApiError ? cause.message : "Something went wrong.");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [zip]);

  return (
    <section className="dashboard">
      <h2>Retailers near {zip}</h2>

      <nav className="dashboard-links">
        <Link to="/search">Search for an item</Link>
        <Link to="/basket">Compare a basket</Link>
      </nav>

      {loading && <p role="status">Checking retailers…</p>}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}

      {health && (
        <>
          <p className="dashboard-meta">
            {formatCheckedAt(health.checked_at, now)} · data source:{" "}
            <b>{health.source}</b>
          </p>
          {health.source === "fixture" && (
            /* Stated up front: nothing here has been read from a live retailer. */
            <p role="note" className="warning">
              Running on local fixtures — these are not live retailer prices.
            </p>
          )}
          <ul className="retailer-grid">
            {health.connector_health.map((connector) => (
              <li
                key={connector.retailer}
                className="retailer-card"
                data-testid="retailer-card"
                data-status={connector.status}
              >
                <h3>{retailerName(connector.retailer)}</h3>
                <span className={`badge tone-${connector.status}`}>
                  {STATUS_TEXT[connector.status] ?? connector.status}
                </span>
                {connector.error_reason && (
                  <p className="retailer-reason">{connector.error_reason}</p>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
