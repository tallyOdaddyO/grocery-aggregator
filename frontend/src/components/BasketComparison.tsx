import type { BasketPlan, CompareBasketResponse, RetailerTrip } from "../types/api";
import { formatCents } from "../lib/format";
import { NOTE_LABEL, retailerName } from "../lib/retailers";
import { PriceTag } from "./PriceTag";

function stops(count: number): string {
  return `${count} ${count === 1 ? "stop" : "stops"}`;
}

function Trip({ trip, now }: { trip: RetailerTrip; now?: Date }) {
  return (
    <li className="trip" data-testid="trip">
      <h4 className="trip-store">
        {retailerName(trip.retailer)}{" "}
        <span className="trip-number">#{trip.store_number}</span>
      </h4>
      <ul className="trip-items">
        {trip.items.map((item) => (
          <li key={item.product_id} className="trip-item">
            <span className="item-title">
              {item.quantity > 1 && <b>{item.quantity}× </b>}
              {item.title}
            </span>
            <span className="item-size">{item.size_raw}</span>
            <PriceTag price={item.price} now={now} />
            <span className="item-total">{formatCents(item.line_total_cents)}</span>
            {item.notes.length > 0 && (
              <ul className="item-notes">
                {item.notes
                  .filter((note) => note in NOTE_LABEL)
                  .map((note) => (
                    <li key={note} className="item-note">
                      {NOTE_LABEL[note]}
                    </li>
                  ))}
              </ul>
            )}
          </li>
        ))}
      </ul>
      <p className="trip-subtotal">Subtotal {formatCents(trip.subtotal_cents)}</p>
    </li>
  );
}

function Plan({ plan, now }: { plan: BasketPlan; now?: Date }) {
  return (
    <>
      <p className="plan-total">
        <span className="plan-amount">{formatCents(plan.total_cents)}</span>
        <span className="plan-stops">{stops(plan.stop_count)}</span>
      </p>
      <ul className="trips">
        {plan.trips.map((trip) => (
          <Trip key={`${trip.retailer}-${trip.store_number}`} trip={trip} now={now} />
        ))}
      </ul>
    </>
  );
}

/**
 * Cheapest single trip against cheapest split.
 *
 * The two plans are always shown together, with the number of stops as prominent
 * as the money: a plan that saves a dollar across three stores is not obviously
 * better, and the interface should not pretend otherwise.
 */
export function BasketComparison({
  data,
  now,
}: {
  data: CompareBasketResponse;
  now?: Date;
}) {
  const degraded = data.connector_health.filter((h) => h.status !== "ok");

  return (
    <section className="basket-comparison">
      {!data.is_complete && (
        <p role="status" className="warning">
          This comparison may be incomplete —{" "}
          {degraded.map((h) => retailerName(h.retailer)).join(", ")} did not fully
          report.
        </p>
      )}

      <div className="plans">
        <article className="plan" data-testid="plan-complete">
          <h3>One stop</h3>
          {data.cheapest_complete ? (
            <Plan plan={data.cheapest_complete} now={now} />
          ) : (
            // Never approximated by mixing stores: that would be a plan the
            // shopper cannot actually execute in a single trip.
            <p className="plan-empty">
              No single store nearby stocks every item in this basket.
            </p>
          )}
        </article>

        <article className="plan" data-testid="plan-split">
          <h3>Shopping around</h3>
          {data.cheapest_split ? (
            <Plan plan={data.cheapest_split} now={now} />
          ) : (
            <p className="plan-empty">
              Nothing in this basket could be sourced nearby.
            </p>
          )}
        </article>
      </div>

      {data.savings_cents !== null && data.savings_cents > 0 && (
        <p className="savings" data-testid="savings">
          Shopping around saves {formatCents(data.savings_cents)}, at the cost of{" "}
          {stops((data.cheapest_split?.stop_count ?? 1) - 1)} more.
        </p>
      )}

      {data.unavailable_items.length > 0 && (
        <div className="unavailable" data-testid="unavailable">
          <h3>Not available nearby</h3>
          <ul>
            {data.unavailable_items.map((item) => (
              <li key={item.query}>
                <b>{item.query}</b> — {item.reason}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
