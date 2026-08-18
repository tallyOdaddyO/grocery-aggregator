"""Shared FastAPI dependencies."""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.orm import Session

from app.db.session import SessionLocal


def get_db_session() -> Iterator[Session]:
    """A request-scoped database session. Overridden in tests."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
