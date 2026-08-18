import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ConfidenceStats } from "../types/api";
import { MatchExplanation } from "./MatchExplanation";

function stats(overrides: Partial<ConfidenceStats> = {}): ConfidenceStats {
  return {
    match_confidence: 1.0,
    match_type: "upc",
    threshold: 0.82,
    veto_checks_passed: ["unit_dimension", "package_size", "brand"],
    veto_checks_failed: [],
    signals: [
      { name: "upc", detail: "UPC exact match (00016000275270)", weight: 1.0 },
      { name: "size", detail: "size equivalent (12 oz ≈ 12 oz)", weight: 0.0 },
    ],
    explanation: "Confidence 100%: UPC exact match (00016000275270)",
    equivalent_count: 4,
    ...overrides,
  };
}

describe("MatchExplanation", () => {
  it("shows the confidence as a percentage", () => {
    render(<MatchExplanation stats={stats()} />);
    expect(screen.getByTestId("confidence")).toHaveTextContent("100%");
  });

  it("names how the match was made", () => {
    render(<MatchExplanation stats={stats()} />);
    expect(screen.getByText(/matched by upc/i)).toBeInTheDocument();
  });

  it("lists every veto check that passed, in readable words", () => {
    render(<MatchExplanation stats={stats()} />);
    expect(screen.getByText("Package size")).toBeInTheDocument();
    expect(screen.getByText("Unit dimension")).toBeInTheDocument();
    expect(screen.getByText("Brand")).toBeInTheDocument();
  });

  it("distinguishes a check that was not evaluated from one that passed", () => {
    render(
      <MatchExplanation
        stats={stats({ veto_checks_passed: ["brand"], match_confidence: 0.75 })}
      />,
    );
    expect(screen.getByText("Brand")).toBeInTheDocument();
    // Size could not be verified, so it must not be shown as cleared.
    expect(screen.queryByText("Package size")).not.toBeInTheDocument();
    expect(screen.getByText(/could not be verified/i)).toBeInTheDocument();
  });

  it("shows each contributing signal", () => {
    render(<MatchExplanation stats={stats()} />);
    expect(screen.getByText(/UPC exact match/)).toBeInTheDocument();
    expect(screen.getByText(/size equivalent/)).toBeInTheDocument();
  });

  it("explains plainly when there is nothing to compare against", () => {
    render(
      <MatchExplanation
        stats={stats({
          match_type: "singleton",
          match_confidence: 0,
          equivalent_count: 0,
          veto_checks_passed: [],
          signals: [],
          explanation: "No equivalent product was found at another retailer.",
        })}
      />,
    );
    expect(screen.getByText(/only retailer/i)).toBeInTheDocument();
    expect(screen.queryByTestId("confidence")).not.toBeInTheDocument();
  });

  it("reports a failed check as the reason for exclusion", () => {
    render(
      <MatchExplanation
        stats={stats({
          match_confidence: 0,
          match_type: "attributes",
          veto_checks_passed: ["brand"],
          veto_checks_failed: ["package_size"],
          signals: [
            { name: "size", detail: "package size differs (12 oz vs 24 oz)", weight: 0 },
          ],
          explanation: "Not equivalent: package size differs (12 oz vs 24 oz)",
        })}
      />,
    );
    expect(screen.getByText(/not equivalent/i)).toBeInTheDocument();
    expect(screen.getByTestId("veto-failed")).toHaveTextContent("Package size");
  });
});
