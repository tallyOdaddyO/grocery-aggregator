import type { ConnectorHealth } from "../types/api";
import { retailerName } from "../lib/retailers";

/**
 * States plainly when a result set is incomplete.
 *
 * A partial search that looks complete is the failure mode this whole system is
 * built to avoid, so the banner is rendered above the results, not tucked away.
 */
export function ConnectorHealthBanner({
  health,
  isComplete,
}: {
  health: ConnectorHealth[];
  isComplete: boolean;
}) {
  if (isComplete) return null;
  const problems = health.filter((h) => h.status !== "ok");
  if (problems.length === 0) return null;

  return (
    <div role="status" className="warning" data-testid="health-banner">
      <p>
        These results are incomplete — {problems.length} of {health.length} retailers
        did not fully report.
      </p>
      <ul>
        {problems.map((problem) => (
          <li key={problem.retailer} data-testid="health-problem">
            <b>{retailerName(problem.retailer)}</b>{" "}
            <span className={`badge tone-${problem.status}`}>{problem.status}</span>{" "}
            {problem.error_reason}
          </li>
        ))}
      </ul>
    </div>
  );
}
