"""Portable column types.

The production target is PostgreSQL; the local dev/test target is SQLite. Rather
than degrading the production schema to the lowest common denominator, these types
compile to the *best available* type per dialect.
"""
from __future__ import annotations

from enum import Enum

from sqlalchemy import JSON, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator

#: JSON payload column.
#:
#: Emits ``JSONB`` on PostgreSQL and ``JSON`` everywhere else (SQLite in dev/test).
#: JSONB is required so attribute blobs and match-signal payloads can carry GIN
#: indexes for containment queries (``attributes @> '{"organic": true}'``).
#: ``with_variant`` is SQLAlchemy's dialect-specific compilation hook: one type
#: object, different DDL per backend, and no branching at the model layer.
JSONVariant = JSON().with_variant(JSONB, "postgresql")


def jsonb_gin_index(name: str, *expressions) -> Index:
    """A GIN index that is emitted on PostgreSQL and skipped everywhere else.

    SQLite has no GIN access method. ``ddl_if`` gates DDL emission on the target
    dialect, so the index is declared once here - visible alongside the model that
    needs it - and simply does not appear when the suite runs on SQLite.
    """
    return Index(name, *expressions, postgresql_using="gin").ddl_if(
        dialect="postgresql"
    )


class EnumString(TypeDecorator):
    """Store a ``StrEnum`` as its plain string value, load it back as the enum.

    Native ``ENUM`` types are avoided deliberately: PostgreSQL enums require a
    migration to add a value, and SQLite has no enum at all. Storing the string
    keeps both backends identical and keeps the values human-readable in the
    database.

    Without this decorator a column declared ``Mapped[RetailerStatus]`` but backed
    by ``String`` silently returns ``str`` on load, so ``row.status.value`` blows up
    and ``row.status is RetailerStatus.ACTIVE`` is quietly always False - a bug that
    only shows up after a round-trip through the database.
    """

    impl = String
    cache_ok = True

    def __init__(self, enum_cls: type[Enum], length: int = 32) -> None:
        self.enum_cls = enum_cls
        super().__init__(length=length)

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, self.enum_cls):
            return value.value
        # Accept a raw string, but only if it is a legal member.
        return self.enum_cls(value).value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return self.enum_cls(value)
