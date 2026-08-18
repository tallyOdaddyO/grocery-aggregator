import { describe, expect, it } from "vitest";
import { formatCents, formatCheckedAt, formatUnitPrice } from "./format";

const NOW = new Date("2026-08-18T12:00:00Z");

function ago(minutes: number): string {
  return new Date(NOW.getTime() - minutes * 60_000).toISOString();
}

describe("formatCheckedAt", () => {
  it("reads 'Checked 18 minutes ago'", () => {
    expect(formatCheckedAt(ago(18), NOW)).toBe("Checked 18 minutes ago");
  });

  it("collapses the last 90 seconds to 'just now'", () => {
    expect(formatCheckedAt(ago(1), NOW)).toBe("Checked just now");
  });

  it("switches to hours, singular and plural", () => {
    expect(formatCheckedAt(ago(60), NOW)).toBe("Checked 1 hour ago");
    expect(formatCheckedAt(ago(60 * 5), NOW)).toBe("Checked 5 hours ago");
  });

  it("switches to days past 48 hours", () => {
    expect(formatCheckedAt(ago(60 * 24 * 3), NOW)).toBe("Checked 3 days ago");
  });

  it("never reports a negative age from clock skew", () => {
    const future = new Date(NOW.getTime() + 60_000).toISOString();
    expect(formatCheckedAt(future, NOW)).toBe("Checked just now");
  });
});

describe("formatCents", () => {
  it("renders whole dollars and cents", () => {
    expect(formatCents(1999)).toBe("$19.99");
    expect(formatCents(350)).toBe("$3.50");
    expect(formatCents(0)).toBe("$0.00");
  });

  it("groups thousands", () => {
    expect(formatCents(123456)).toBe("$1,234.56");
  });

  it("returns a dash rather than $0.00 when there is no price", () => {
    expect(formatCents(null)).toBe("—");
  });
});

describe("formatUnitPrice", () => {
  it("renders the measure alongside the price", () => {
    expect(formatUnitPrice(467, "lb")).toBe("$4.67/lb");
  });

  it("prettifies fl_oz", () => {
    expect(formatUnitPrice(50, "fl_oz")).toBe("$0.50/fl oz");
  });

  it("returns null when no unit price is known", () => {
    expect(formatUnitPrice(null, "lb")).toBeNull();
    expect(formatUnitPrice(467, "unknown")).toBeNull();
  });
});
