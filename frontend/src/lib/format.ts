/** Display formatting. Money arrives as integer cents and is never floated. */

const PRETTY_UOM: Record<string, string> = {
  fl_oz: "fl oz",
  ct: "count",
};

/**
 * "Checked 18 minutes ago", from an ISO timestamp.
 *
 * Mirrors the backend's freshness wording so a price never appears to have been
 * checked at two different times depending on which surface renders it.
 */
export function formatCheckedAt(timestamp: string, now: Date = new Date()): string {
  const observed = new Date(timestamp).getTime();
  if (Number.isNaN(observed)) return "Checked at an unknown time";

  // Clamped at zero: a clock skew must never read "checked in 3 minutes".
  const seconds = Math.max(0, Math.floor((now.getTime() - observed) / 1000));

  if (seconds < 90) return "Checked just now";
  if (seconds < 3600) return `Checked ${Math.floor(seconds / 60)} minutes ago`;
  if (seconds < 172_800) {
    const hours = Math.floor(seconds / 3600);
    return `Checked ${hours} hour${hours === 1 ? "" : "s"} ago`;
  }
  const days = Math.floor(seconds / 86_400);
  return `Checked ${days} day${days === 1 ? "" : "s"} ago`;
}

/** Integer cents to a dollar string. Null becomes an em dash, never "$0.00". */
export function formatCents(cents: number | null | undefined): string {
  if (cents === null || cents === undefined) return "—";
  return `$${(cents / 100).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

/**
 * "$4.67/lb", or null when no unit price could be derived.
 *
 * Returning null rather than a placeholder keeps callers from rendering a
 * comparison figure for a package whose size we could not parse.
 */
export function formatUnitPrice(
  cents: number | null | undefined,
  measure: string | null | undefined,
): string | null {
  if (cents === null || cents === undefined) return null;
  if (!measure || measure === "unknown") return null;
  return `${formatCents(cents)}/${PRETTY_UOM[measure] ?? measure}`;
}
