"""Test fixtures: an isolated in-memory database per test."""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

# Must be set before app.core.config is imported anywhere.
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

import app.models  # noqa: E402,F401
from app.core.enums import PriceProvenance, RetailerStatus  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.models import (  # noqa: E402
    Price, PriceObservation, Product, ProductVariant, Retailer, Store,
)


@pytest.fixture
def engine():
    eng = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(eng, "connect")
    def _fk_on(dbapi_conn, _rec):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine) -> Session:
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        yield session


@pytest.fixture
def publix(db) -> Retailer:
    r = Retailer(
        slug="publix", name="Publix", status=RetailerStatus.ACTIVE,
        supports_online_pricing=True,
    )
    db.add(r)
    db.flush()
    return r


@pytest.fixture
def publix_store(db, publix) -> Store:
    s = Store(
        retailer_id=publix.id, store_number="0001", zip="33009",
        city="Hallandale Beach", state="FL", is_primary_for_zip=True,
        address_verified=False,
    )
    db.add(s)
    db.flush()
    return s


@pytest.fixture
def variant(db, publix) -> ProductVariant:
    p = Product(
        upc="00016000275270", normalized_name="cheerios original",
        display_name="Cheerios Original", brand="General Mills",
        category="cereal", attributes={"organic": False},
    )
    db.add(p)
    db.flush()
    v = ProductVariant(
        product_id=p.id, retailer_id=publix.id, retailer_sku="PUB-1001",
        upc="00016000275270", display_name="Cheerios Original 12 oz",
        size_text="12 oz", pack_count=1, net_content_value=12.0,
        net_content_uom="oz", base_quantity=340.194, base_uom="g",
        uom_kind="mass",
    )
    db.add(v)
    db.flush()
    return v


@pytest.fixture
def utcnow() -> datetime:
    return datetime.now(timezone.utc)
