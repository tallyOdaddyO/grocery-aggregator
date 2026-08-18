"""The seed script must be re-runnable and must not invent facts."""
from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from seed import RETAILERS, seed  # noqa: E402

from app.core.enums import RetailerStatus  # noqa: E402
from app.models import Retailer, Store  # noqa: E402


def test_seeds_all_eight_target_retailers(db):
    seed(db)
    slugs = set(db.scalars(select(Retailer.slug)).all())
    assert slugs == {
        "walmart", "costco", "bjs", "publix",
        "winn_dixie", "fresco_y_mas", "presidente", "rey_chavez",
    }
    assert len(RETAILERS) == 8


def test_seed_is_idempotent(db):
    seed(db)
    seed(db)
    seed(db)
    assert db.scalar(select(func.count(Retailer.id))) == 8
    assert db.scalar(select(func.count(Store.id))) == 8


def test_no_retailer_claims_to_be_active_before_a_connector_exists(db):
    seed(db)
    for r in db.scalars(select(Retailer)).all():
        assert r.status is RetailerStatus.UNAVAILABLE
        assert r.status_reason, f"{r.slug} must explain why it is unavailable"
        assert r.supports_online_pricing is False


def test_no_store_address_is_fabricated(db):
    seed(db)
    for s in db.scalars(select(Store)).all():
        assert s.address_verified is False
        assert s.address_line1 is None
        assert s.latitude is None and s.longitude is None
        # The ZIP and city are the only location facts we actually assert.
        assert s.zip == "33009"
        assert s.city == "Hallandale Beach"


def test_membership_gated_retailers_are_flagged(db):
    seed(db)
    by_slug = {r.slug: r for r in db.scalars(select(Retailer)).all()}
    assert by_slug["costco"].requires_membership is True
    assert by_slug["bjs"].requires_membership is True
    assert by_slug["publix"].requires_membership is False


def test_each_retailer_has_exactly_one_primary_store_for_the_zip(db):
    seed(db)
    for r in db.scalars(select(Retailer)).all():
        primaries = db.scalars(
            select(Store).where(
                Store.retailer_id == r.id,
                Store.zip == "33009",
                Store.is_primary_for_zip.is_(True),
            )
        ).all()
        assert len(primaries) == 1, f"{r.slug} needs exactly one primary store"
