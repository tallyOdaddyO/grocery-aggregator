import type { ConfidenceStats } from "../types/api";

/** Machine check names rendered as words a shopper can read. */
const CHECK_LABEL: Record<string, string> = {
  unit_dimension: "Unit dimension",
  package_size: "Package size",
  variant_attributes: "Variant attributes",
  brand: "Brand",
};

const ALL_CHECKS = ["unit_dimension", "package_size", "variant_attributes", "brand"];

const STAGE_LABEL: Record<string, string> = {
  upc: "UPC",
  attributes: "product attributes",
  fuzzy: "name and size",
  singleton: "nothing to compare",
};

function label(check: string): string {
  return CHECK_LABEL[check] ?? check;
}

/**
 * Explains why two products were treated as the same thing - or why they were not.
 *
 * A check that was never evaluated is reported as unverified rather than being
 * omitted silently, so "passed" always means "we actually checked it".
 */
export function MatchExplanation({ stats }: { stats: ConfidenceStats }) {
  const isSingleton = stats.match_type === "singleton" || stats.equivalent_count === 0;
  const vetoed = stats.veto_checks_failed.length > 0;

  if (isSingleton && !vetoed) {
    return (
      <div className="match-explanation" data-testid="match-explanation">
        <p className="match-summary">
          This is the only retailer nearby carrying this exact product, so there is
          nothing to compare it against.
        </p>
      </div>
    );
  }

  const unverified = ALL_CHECKS.filter(
    (check) =>
      !stats.veto_checks_passed.includes(check) &&
      !stats.veto_checks_failed.includes(check),
  );

  return (
    <div className="match-explanation" data-testid="match-explanation">
      {vetoed ? (
        <p className="match-summary tone-veto">Not equivalent — {stats.explanation}</p>
      ) : (
        <p className="match-summary">
          <span className="confidence" data-testid="confidence">
            {Math.round(stats.match_confidence * 100)}%
          </span>{" "}
          confident, matched by {STAGE_LABEL[stats.match_type] ?? stats.match_type},
          across {stats.equivalent_count} other{" "}
          {stats.equivalent_count === 1 ? "retailer" : "retailers"}.
        </p>
      )}

      {stats.veto_checks_passed.length > 0 && (
        <ul className="checks checks-passed" data-testid="veto-passed">
          {stats.veto_checks_passed.map((check) => (
            <li key={check} className="check check-pass">
              {label(check)}
            </li>
          ))}
        </ul>
      )}

      {stats.veto_checks_failed.length > 0 && (
        <ul className="checks checks-failed" data-testid="veto-failed">
          {stats.veto_checks_failed.map((check) => (
            <li key={check} className="check check-fail">
              {label(check)}
            </li>
          ))}
        </ul>
      )}

      {unverified.length > 0 && (
        <p className="checks-unverified">
          {unverified.map(label).join(", ")} could not be verified for this product.
        </p>
      )}

      {stats.signals.length > 0 && (
        <ul className="signals">
          {stats.signals.map((signal) => (
            <li key={`${signal.name}-${signal.detail}`} className="signal">
              {signal.detail}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
