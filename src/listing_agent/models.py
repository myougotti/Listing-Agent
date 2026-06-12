"""Provider-agnostic listing model.

The Listing class is the boundary between providers and the rest of the app.
Provider adapters normalize their wire format into this shape so nothing
downstream depends on Zillow-vs-Redfin-vs-anything-else.

If you need a field that is not here, prefer reading it from `raw` rather
than adding a column. That keeps the model stable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Listing:
    # Stable identifier from the source. Zillow uses zpid.
    zpid: str
    zip_code: str
    price: int  # USD, whole dollars
    beds: float
    baths: float
    sqft: int | None
    status: str  # e.g. FOR_SALE, PENDING, SOLD
    home_type: str | None  # e.g. SINGLE_FAMILY, TOWNHOUSE
    url: str
    address: str
    days_on_market: int | None = None
    image_url: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    lot_sqft: int | None = None
    description: str | None = None  # often absent in search results
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReasonerVerdict:
    score: int  # 0..100
    verdict: str  # STRONG_MATCH | MATCH | SOFT_PASS | REJECT
    summary: str
    highlights: list[str]
    red_flags: list[str]
    needs_human: bool
