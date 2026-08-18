"""The connector registry: the single list the search fan-out iterates."""
from __future__ import annotations

from app.connectors.base import BaseRetailerConnector
from app.connectors.supermarkets import (
    FrescoYMasConnector, PresidenteConnector, PublixConnector,
    ReyChavezConnector, WinnDixieConnector,
)
from app.connectors.warehouse_clubs import (
    BJsConnector, CostcoConnector, WalmartConnector,
)

CONNECTOR_CLASSES: list[type[BaseRetailerConnector]] = [
    WalmartConnector,
    CostcoConnector,
    BJsConnector,
    PublixConnector,
    WinnDixieConnector,
    FrescoYMasConnector,
    PresidenteConnector,
    ReyChavezConnector,
]


def build_connectors(source: str = "fixture") -> list[BaseRetailerConnector]:
    return [cls(source=source) for cls in CONNECTOR_CLASSES]


def connector_by_slug(slug: str, source: str = "fixture") -> BaseRetailerConnector:
    for cls in CONNECTOR_CLASSES:
        if cls.slug == slug:
            return cls(source=source)
    raise KeyError(f"no connector registered for slug {slug!r}")
