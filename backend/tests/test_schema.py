"""Phase 2 gate: the schema behaves the way the architecture claims it does."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.schema import CreateIndex, CreateTable

import app.models  # noqa: F401
from app.core.enums import (
    PriceProvenance, PROVENANCE_RANK, PromotionType, RetailerStatus,
)
from app.db.base import Base
from app.models import (
    Price, PriceObservation, Product, ProductVariant, Retailer, Store,
)


class TestDialectPortability:
    """The JSONB-on-PostgreSQL / JSON-on-SQLite requirement."""

    def test_json_columns_compile_to_jsonb_on_postgres(self):
        ddl = "\n".join(
            str(CreateTable(t).compile(dialect=postgresql.dialect()))
            for t in Base.metadata.sorted_tables
        )
        assert "JSONB" in ddl
        # No bare JSON should survive on PostgreSQL.
        assert " JSON," not in ddl and " JSON\n" not in ddl

    def test_json_columns_compile_to_json_on_sqlite(self):
        from sqlalchemy.dialects import sqlite

        ddl = str(
            CreateTable(Base.metadata.tables["products"]).compile(
                dialect=sqlite.dialect()
            )
        )
        assert "JSON" in ddl and "JSONB" not in ddl

    def test_gin_indexes_are_declared_for_postgres(self):
        gin = [
            i
            for t in Base.metadata.sorted_tables
            for i in t.indexes
            if i.dialect_options.get("postgresql", {}).get("using") == "gin"
        ]
        names = {i.name for i in gin}
        assert names == {"ix_products_attributes_gin", "ix_matches_signals_gin"}
        for i in gin:
            sql = str(CreateIndex(i).compile(dialect=postgresql.dialect()))
            assert "USING gin" in sql

    def test_gin_indexes_are_absent_on_sqlite(self, engine):
        insp = inspect(engine)
        all_idx = {
            i["name"] for t in insp.get_table_names() for i in insp.get_indexes(t)
        }
        assert not any("gin" in n for n in all_idx)

    def test_whole_schema_creates_on_sqlite(self, engine):
        assert len(inspect(engine).get_table_names()) == 9


class TestEnumRoundTrip:
    """A column declared Mapped[SomeEnum] must load back as that enum."""

    def test_retailer_status_returns_enum_not_str(self, db):
        db.add(Retailer(slug="x", name="X", status=RetailerStatus.DEGRADED))
        db.commit()
        db.expire_all()
        loaded = db.scalar(select(Retailer).where(Retailer.slug == "x"))
        assert isinstance(loaded.status, RetailerStatus)
        assert loaded.status is RetailerStatus.DEGRADED
        assert loaded.status.value == "degraded"

    def test_provenance_returns_enum_after_reload(self, db, publix_store, variant):
        db.add(
            Price(
                variant_id=variant.id, store_id=publix_store.id, price_cents=499,
                provenance=PriceProvenance.VERIFIED_IN_STORE,
                is_verified_in_store=True,
                observed_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
        db.expire_all()
        p = db.scalar(select(Price))
        assert p.provenance is PriceProvenance.VERIFIED_IN_STORE
        assert p.promotion_type is PromotionType.NONE

    def test_illegal_enum_value_is_rejected(self, db):
        """A bogus status must never reach the database.

        SQLAlchemy wraps the type's ValueError in a StatementError; what matters is
        that the INSERT fails rather than persisting an unknown status that later
        code would have to guess at.
        """
        db.add(Retailer(slug="bad", name="Bad", status="totally-made-up"))
        with pytest.raises(StatementError) as exc:
            db.commit()
        assert isinstance(exc.value.orig, ValueError)
        assert "not a valid RetailerStatus" in str(exc.value.orig)


class TestProvenance:
    def test_ranking_orders_in_store_above_online_above_estimated(self):
        assert (
            PROVENANCE_RANK[PriceProvenance.VERIFIED_IN_STORE]
            < PROVENANCE_RANK[PriceProvenance.VERIFIED_ONLINE]
            < PROVENANCE_RANK[PriceProvenance.DELIVERY_PRICE]
            < PROVENANCE_RANK[PriceProvenance.ESTIMATED]
            < PROVENANCE_RANK[PriceProvenance.STALE]
        )

    def test_every_provenance_value_is_ranked(self):
        assert set(PROVENANCE_RANK) == set(PriceProvenance)

    def test_provenance_is_required(self, db, publix_store, variant):
        db.add(
            Price(
                variant_id=variant.id, store_id=publix_store.id, price_cents=499,
                observed_at=datetime.now(timezone.utc),
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()


class TestPriceIntegrity:
    def test_money_is_exact_integer_cents(self, db, publix_store, variant):
        db.add(
            Price(
                variant_id=variant.id, store_id=publix_store.id, price_cents=1999,
                provenance=PriceProvenance.VERIFIED_ONLINE,
                observed_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
        total = sum(db.scalar(select(Price)).price_cents for _ in range(3))
        assert total == 5997  # exact; 19.99*3 in float is 59.970000000000006

    def test_one_price_per_variant_per_store(self, db, publix_store, variant):
        now = datetime.now(timezone.utc)
        for _ in range(2):
            db.add(
                Price(
                    variant_id=variant.id, store_id=publix_store.id, price_cents=499,
                    provenance=PriceProvenance.VERIFIED_ONLINE, observed_at=now,
                )
            )
        with pytest.raises(IntegrityError):
            db.commit()

    def test_price_cannot_reference_a_missing_store(self, db, variant):
        db.add(
            Price(
                variant_id=variant.id, store_id=99999, price_cents=499,
                provenance=PriceProvenance.VERIFIED_ONLINE,
                observed_at=datetime.now(timezone.utc),
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()


class TestObservationLog:
    def test_history_survives_a_price_update(self, db, publix_store, variant):
        t0 = datetime.now(timezone.utc) - timedelta(days=1)
        t1 = datetime.now(timezone.utc)
        price = Price(
            variant_id=variant.id, store_id=publix_store.id, price_cents=499,
            provenance=PriceProvenance.VERIFIED_ONLINE, observed_at=t0,
        )
        db.add_all(
            [
                price,
                PriceObservation(
                    variant_id=variant.id, store_id=publix_store.id,
                    price_cents=499, provenance=PriceProvenance.VERIFIED_ONLINE,
                    observed_at=t0, source="publix:fixture",
                ),
            ]
        )
        db.commit()

        price.price_cents = 429
        price.observed_at = t1
        db.add(
            PriceObservation(
                variant_id=variant.id, store_id=publix_store.id, price_cents=429,
                provenance=PriceProvenance.VERIFIED_ONLINE, observed_at=t1,
                source="publix:fixture",
            )
        )
        db.commit()

        history = db.scalars(
            select(PriceObservation).order_by(PriceObservation.observed_at)
        ).all()
        assert [o.price_cents for o in history] == [499, 429]
        assert db.scalar(select(Price)).price_cents == 429

    def test_observation_keeps_raw_payload_json(self, db, publix_store, variant):
        payload = {"sku": "PUB-1001", "raw_price": "$4.99", "nested": {"ad": True}}
        db.add(
            PriceObservation(
                variant_id=variant.id, store_id=publix_store.id, price_cents=499,
                provenance=PriceProvenance.VERIFIED_ONLINE,
                observed_at=datetime.now(timezone.utc), raw_payload=payload,
            )
        )
        db.commit()
        db.expire_all()
        assert db.scalar(select(PriceObservation)).raw_payload == payload


class TestLocationDiscipline:
    def test_store_number_is_unique_per_retailer(self, db, publix):
        db.add_all(
            [
                Store(retailer_id=publix.id, store_number="D1", zip="33009"),
                Store(retailer_id=publix.id, store_number="D1", zip="33009"),
            ]
        )
        with pytest.raises(IntegrityError):
            db.commit()

    def test_seeded_addresses_are_marked_unverified(self, db, publix_store):
        assert publix_store.address_verified is False
        assert publix_store.address_line1 is None

    def test_prices_attach_to_stores_not_retailers(self):
        assert "store_id" in Price.__table__.c
        assert "retailer_id" not in Price.__table__.c


class TestProductAttributes:
    def test_attributes_json_round_trips(self, db, publix):
        p = Product(
            normalized_name="milk whole", display_name="Whole Milk",
            attributes={"organic": True, "fat": "whole", "tags": ["a", "b"]},
        )
        db.add(p)
        db.commit()
        db.expire_all()
        loaded = db.scalar(select(Product))
        assert loaded.attributes["organic"] is True
        assert loaded.attributes["tags"] == ["a", "b"]

    def test_upc_is_optional(self, db):
        db.add(Product(normalized_name="bananas", display_name="Bananas"))
        db.commit()
        assert db.scalar(select(Product)).upc is None
