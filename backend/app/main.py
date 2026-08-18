"""FastAPI application entry point."""
from __future__ import annotations

import logging

from fastapi import FastAPI

from app.api.v1.basket import router as basket_router
from app.api.v1.product import router as product_router
from app.api.v1.search import router as search_router
from app.core.config import get_settings

settings = get_settings()
logging.basicConfig(level=settings.log_level)

app = FastAPI(
    title="RetailScout",
    version="0.1.0",
    description=(
        "Local grocery price aggregator for ZIP 33009. Every price carries its "
        "provenance and age; every incomplete search says so."
    ),
)

app.include_router(search_router)
app.include_router(product_router)
app.include_router(basket_router)


@app.get("/api/v1/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "target_zip": settings.target_zip,
            "source": settings.retailscout_source}
