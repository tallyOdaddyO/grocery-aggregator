import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ApiError, searchProducts } from "../lib/api";
import { ConnectorHealthBanner } from "../components/ConnectorHealthBanner";
import { MatchGroupCard } from "../components/MatchGroupCard";
import type { SearchResponse } from "../types/api";

export function SearchPage({ zip = "33009", now }: { zip?: string; now?: Date }) {
  const [params, setParams] = useSearchParams();
  const query = params.get("q") ?? "";
  const [draft, setDraft] = useState(query);
  const [data, setData] = useState<SearchResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => setDraft(query), [query]);

  useEffect(() => {
    if (!query) {
      setData(null);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    searchProducts(query, zip, controller.signal)
      .then((response) => setData(response))
      .catch((cause) => {
        if (controller.signal.aborted) return;
        setData(null);
        setError(cause instanceof ApiError ? cause.message : "Search failed.");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [query, zip]);

  return (
    <section className="search-page">
      <form
        className="search-form"
        onSubmit={(event) => {
          event.preventDefault();
          setParams(draft ? { q: draft } : {});
        }}
      >
        <label htmlFor="q">Search groceries</label>
        <input
          id="q"
          name="q"
          value={draft}
          placeholder="e.g. cheerios"
          onChange={(event) => setDraft(event.target.value)}
        />
        <button type="submit">Search</button>
      </form>

      {loading && <p role="status">Searching every retailer…</p>}
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
          {data.results.length === 0 ? (
            <p className="empty" data-testid="no-results">
              No retailer near {data.zip_code} returned a match for “{data.query}”.
            </p>
          ) : (
            <div className="groups">
              {data.results.map((group) => (
                <MatchGroupCard key={group.group_id} group={group} now={now} />
              ))}
            </div>
          )}
        </>
      )}
    </section>
  );
}
