"""Provider interface.

Add new sources (Redfin scraper, APIllow, Zillapi, Apify actor) by
implementing this Protocol. The pipeline only ever holds a `Provider`.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from ..config import HardFilters
from ..models import Listing


class Provider(Protocol):
    name: str

    def search(self, zip_code: str, filters: HardFilters) -> Iterable[Listing]:
        """Yield listings for one zip. Implementations should:

        - Push as many filters server-side as the API supports, to save quota.
        - Set `Listing.raw` to the source-native dict for future reference.
        - Yield (not return a list) so callers can short-circuit on quota."""
        ...
