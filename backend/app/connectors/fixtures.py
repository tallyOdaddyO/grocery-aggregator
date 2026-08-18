"""Fixture loading and the small parsing helpers every adapter needs."""
from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path

FIXTURE_ROOT = Path(__file__).resolve().parents[3] / "fixtures"


class FixtureMissingError(FileNotFoundError):
    pass


def load_json(slug: str, filename: str) -> dict:
    path = FIXTURE_ROOT / slug / filename
    if not path.exists():
        raise FixtureMissingError(f"missing fixture: {path}")
    return json.loads(path.read_text())


def load_text(slug: str, filename: str) -> str:
    path = FIXTURE_ROOT / slug / filename
    if not path.exists():
        raise FixtureMissingError(f"missing fixture: {path}")
    return path.read_text()


def clean_title(raw: str | None) -> str:
    """Undo the usual title damage: HTML entities, symbols, doubled whitespace."""
    if not raw:
        return ""
    text = html.unescape(str(raw))
    text = text.replace("®", " ").replace("™", " ").replace("©", " ")
    return " ".join(text.split())


#: "$4.99", "4.99", "2 for $7.00", "2 x $4.00", "2/$5", "Call for price".
_MULTI_BUY = re.compile(
    r"(\d+)\s*(?:for|x|/)\s*\$?\s*(\d+(?:\.\d{1,2})?)", re.IGNORECASE
)
_PLAIN = re.compile(r"\$?\s*(\d+(?:\.\d{1,2})?)")


def parse_price_text(text) -> tuple[int | None, int | None]:
    """Parse a price string into (unit price in cents, multi-buy quantity).

    Returns ``(None, None)`` for prose like "Call for price" or "not available".
    A multi-buy ("2 for $7.00") yields the per-unit price, because that is what a
    shopper pays for one - but the quantity is returned too, so the caller can say
    the deal requires buying two.
    """
    if text is None:
        return None, None
    if isinstance(text, (int, float)):
        return (int(round(float(text) * 100)), None) if text > 0 else (None, None)

    raw = str(text).strip()
    if not raw:
        return None, None

    if m := _MULTI_BUY.search(raw):
        quantity, total = int(m.group(1)), float(m.group(2))
        if quantity > 0 and total > 0:
            return int(round(total * 100 / quantity)), quantity

    if m := _PLAIN.search(raw):
        value = float(m.group(1))
        return (int(round(value * 100)), None) if value > 0 else (None, None)

    return None, None


def parse_date(value) -> datetime | None:
    """Parse a date that is very often missing. Absence is never invented."""
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    for parser in (datetime.fromisoformat,):
        try:
            parsed = parser(text)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def captured_at(payload: dict) -> datetime:
    """Observation time from the fixture, falling back to now."""
    meta = payload.get("_meta") or {}
    return parse_date(meta.get("captured_at")) or datetime.now(timezone.utc)


def matches(term: str, *fields: str | None) -> bool:
    """Naive relevance filter for fixture search."""
    needle = " ".join((term or "").lower().split())
    if not needle:
        return True
    haystack = " ".join(f.lower() for f in fields if f)
    return all(word in haystack for word in needle.split())
