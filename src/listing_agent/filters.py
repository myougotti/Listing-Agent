"""Pure functions over a Listing. No I/O, easy to test.

Server-side filters in the provider remove most rejections. These run as a
second-pass safety net and to enforce criteria the API does not support
(e.g. min_sqft on some plans).
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import HardFilters
from .models import Listing


@dataclass(frozen=True)
class FilterResult:
    passed: bool
    reasons: list[str]  # populated only on failure


def check(listing: Listing, f: HardFilters) -> FilterResult:
    reasons: list[str] = []

    if f.max_price is not None and listing.price > f.max_price:
        reasons.append(f"price {listing.price} > max {f.max_price}")
    if f.min_price is not None and listing.price < f.min_price:
        reasons.append(f"price {listing.price} < min {f.min_price}")
    if f.min_beds is not None and listing.beds < f.min_beds:
        reasons.append(f"beds {listing.beds} < min {f.min_beds}")
    if f.min_baths is not None and listing.baths < f.min_baths:
        reasons.append(f"baths {listing.baths} < min {f.min_baths}")

    if f.min_sqft is not None:
        if listing.sqft is None:
            # Unknown sqft when a minimum is set is treated as ambiguous,
            # not a fail. Reasoner can flag it.
            pass
        elif listing.sqft < f.min_sqft:
            reasons.append(f"sqft {listing.sqft} < min {f.min_sqft}")
    if f.max_sqft is not None and listing.sqft is not None and listing.sqft > f.max_sqft:
        reasons.append(f"sqft {listing.sqft} > max {f.max_sqft}")

    if f.exclude_statuses and listing.status in f.exclude_statuses:
        reasons.append(f"status {listing.status} is excluded")

    return FilterResult(passed=not reasons, reasons=reasons)
