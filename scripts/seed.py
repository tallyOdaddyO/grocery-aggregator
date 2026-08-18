"""Seed the 8 target retailers and their placeholder Hallandale Beach stores.

HONESTY POLICY
--------------
Chain-level facts below (name, membership requirement) are stable and verifiable.
Store-level facts - street address, store number, coordinates - are NOT invented.
Every seeded store is created with ``address_verified=False`` and a null address;
a real address is written only once a connector or a human confirms it against a
real source. A plausible-looking fake address would silently poison every price
attached to it, which is worse than an obvious blank.

Likewise every retailer starts as ``unavailable``: no connector exists yet
(Phase 4), so claiming ``active`` would assert a capability we do not have.

Idempotent - safe to re-run.
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.enums import RetailerStatus  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.models import Retailer, Store  # noqa: E402

TARGET_ZIP = "33009"
TARGET_CITY = "Hallandale Beach"
TARGET_STATE = "FL"

NOT_IMPLEMENTED = "Connector not implemented yet (Phase 4). No prices are collected."

#: slug, display name, requires_membership, expected difficulty note
RETAILERS: list[dict] = [
    {
        "slug": "walmart",
        "name": "Walmart",
        "requires_membership": False,
        "status_reason": NOT_IMPLEMENTED
        + " Expect strong bot protection; likely fixture-only.",
    },
    {
        "slug": "costco",
        "name": "Costco Wholesale",
        "requires_membership": True,
        "status_reason": NOT_IMPLEMENTED
        + " Warehouse-scoped and membership-gated pricing.",
    },
    {
        "slug": "bjs",
        "name": "BJ's Wholesale Club",
        "requires_membership": True,
        "status_reason": NOT_IMPLEMENTED + " Club-scoped, membership-gated pricing.",
    },
    {
        "slug": "publix",
        "name": "Publix",
        "requires_membership": False,
        "status_reason": NOT_IMPLEMENTED
        + " Store-scoped weekly ad is the most promising signal.",
    },
    {
        "slug": "winn_dixie",
        "name": "Winn-Dixie",
        "requires_membership": False,
        "status_reason": NOT_IMPLEMENTED + " Store-scoped digital circular.",
    },
    {
        "slug": "fresco_y_mas",
        "name": "Fresco y Mas",
        "requires_membership": False,
        "status_reason": NOT_IMPLEMENTED
        + " Shares a platform family with Winn-Dixie.",
    },
    {
        "slug": "presidente",
        "name": "Presidente Supermarket",
        "requires_membership": False,
        "status_reason": NOT_IMPLEMENTED
        + " Regional chain; circular may be PDF/image only.",
    },
    {
        "slug": "rey_chavez",
        "name": "Rey Chavez Distributors",
        "requires_membership": False,
        "status_reason": NOT_IMPLEMENTED
        + " Wholesale distributor rather than retail; consumer pricing may not exist.",
    },
]


def seed(session: Session) -> tuple[int, int]:
    retailers_written = stores_written = 0

    for spec in RETAILERS:
        retailer = session.scalar(
            select(Retailer).where(Retailer.slug == spec["slug"])
        )
        if retailer is None:
            retailer = Retailer(slug=spec["slug"], name=spec["name"])
            session.add(retailer)
            retailers_written += 1

        retailer.name = spec["name"]
        retailer.requires_membership = spec["requires_membership"]
        # No connector exists yet, so no retailer may claim to be active.
        retailer.status = RetailerStatus.UNAVAILABLE
        retailer.status_reason = spec["status_reason"]
        retailer.supports_online_pricing = False
        session.flush()

        # One placeholder store per retailer for the target ZIP. Address is left
        # null on purpose - see the honesty policy in this module's docstring.
        store_number = f"UNVERIFIED-{TARGET_ZIP}"
        store = session.scalar(
            select(Store).where(
                Store.retailer_id == retailer.id, Store.store_number == store_number
            )
        )
        if store is None:
            store = Store(retailer_id=retailer.id, store_number=store_number)
            session.add(store)
            stores_written += 1

        store.name = f"{spec['name']} - {TARGET_CITY} (location unconfirmed)"
        store.address_line1 = None
        store.city = TARGET_CITY
        store.state = TARGET_STATE
        store.zip = TARGET_ZIP
        store.latitude = None
        store.longitude = None
        store.is_primary_for_zip = True
        store.address_verified = False

    session.commit()
    return retailers_written, stores_written


def main() -> None:
    settings = get_settings()
    print(f"Seeding {engine.url.render_as_string(hide_password=True)}")
    with SessionLocal() as session:
        created_r, created_s = seed(session)
        total_r = session.scalar(select(Retailer).order_by(Retailer.id)) is not None
        retailers = session.scalars(select(Retailer).order_by(Retailer.slug)).all()

    print(f"Retailers created: {created_r}  Stores created: {created_s}")
    print(f"\nTarget ZIP {TARGET_ZIP} ({TARGET_CITY}, {TARGET_STATE})")
    print(f"{'slug':<14} {'status':<12} {'member':<7} address")
    print("-" * 72)
    for r in retailers:
        member = "yes" if r.requires_membership else "no"
        print(f"{r.slug:<14} {r.status.value:<12} {member:<7} unverified (null)")
    print(
        "\nAll retailers are 'unavailable' and all addresses are unverified by design."
        "\nNothing here asserts a price or a location we have not actually confirmed."
    )


if __name__ == "__main__":
    main()
