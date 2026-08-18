"""GET /api/v1/health - the page that reports broken connectors must not break."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.v1.health import get_connectors
from app.connectors.base import BaseRetailerConnector, StoreRef
from app.connectors.registry import build_connectors
from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


class _Stub(BaseRetailerConnector):
    def __init__(self, slug, behaviour="ok"):
        super().__init__()
        self.slug, self.name, self.behaviour = slug, slug.title(), behaviour

    def resolve_store(self, zip_code):
        if self.behaviour == "raises":
            raise ConnectionError("store locator unreachable")
        if self.behaviour == "no_store":
            return None
        return StoreRef(retailer_slug=self.slug, store_number="1", zip=zip_code)

    def fetch_raw(self, term, store):
        return []

    def parse_item(self, raw, store):
        return None


def test_reports_all_eight_retailers(client):
    body = client.get("/api/v1/health").json()
    assert body["status"] == "ok"
    assert body["target_zip"] == "33009"
    assert {h["retailer"] for h in body["connector_health"]} == {
        "walmart", "costco", "bjs", "publix",
        "winn_dixie", "fresco_y_mas", "presidente", "rey_chavez",
    }


def test_all_connectors_resolve_a_store_for_the_target_zip(client):
    body = client.get("/api/v1/health").json()
    assert all(h["status"] == "ok" for h in body["connector_health"])


def test_unknown_zip_marks_every_connector_unavailable(client):
    body = client.get("/api/v1/health", params={"zip": "99999"}).json()
    assert all(h["status"] == "unavailable" for h in body["connector_health"])
    assert all(h["error_reason"] for h in body["connector_health"])


def test_a_raising_connector_is_reported_not_propagated(client):
    app.dependency_overrides[get_connectors] = lambda: [
        _Stub("walmart"), _Stub("publix", "raises"), _Stub("costco", "no_store"),
    ]
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    by_slug = {h["retailer"]: h for h in response.json()["connector_health"]}
    assert by_slug["walmart"]["status"] == "ok"
    assert by_slug["publix"]["status"] == "unavailable"
    assert "unreachable" in by_slug["publix"]["error_reason"]
    assert by_slug["costco"]["status"] == "unavailable"


def test_health_does_not_fetch_prices(client):
    """The dashboard must stay cheap and must not masquerade as a refresh."""
    calls = {"n": 0}
    connectors = build_connectors()
    for connector in connectors:
        original = connector.fetch_raw

        def counted(term, store, _o=original):
            calls["n"] += 1
            return _o(term, store)

        connector.fetch_raw = counted
    app.dependency_overrides[get_connectors] = lambda: connectors

    client.get("/api/v1/health")
    assert calls["n"] == 0
