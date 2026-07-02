"""Redfin provider (unofficial endpoints, no API key).

Redfin has no public API. Its own website talks to "stingray" JSON
endpoints, and this adapter speaks to two of them the same way a browser
does:

  1. GET https://www.redfin.com/stingray/do/location-autocomplete
         ?location={zip}&v=2
     Resolves a zip code to Redfin's internal region id. Rows carry an id
     like "2_18868" where the prefix is the region type (2 = zipcode).

  2. GET https://www.redfin.com/stingray/api/gis
         ?al=1&region_id={id}&region_type=2&num_homes=350&status=9&v=8
     Returns active listings for the region as payload.homes.

Two quirks to know before you edit:

- Every stingray response is prefixed with the literal string "{}&&" (an
  old anti-JSON-hijacking guard). Strip it before json.loads.
- Requests without a browser-like User-Agent get 403'd, so we send one.

Because these endpoints are undocumented, this provider needs the same
stage-1 sanity check the Zillow one got: run one real search and confirm
the parse before trusting it. If Redfin changes the schema, only this
file should need edits. Same legal caveat as the README: automated access
is against Redfin's terms; personal use is your own risk call.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterable
from typing import Any

import requests

from ..config import HardFilters
from ..models import Listing

log = logging.getLogger(__name__)

BASE_URL = "https://www.redfin.com"
AUTOCOMPLETE_PATH = "/stingray/do/location-autocomplete"
SEARCH_PATH = "/stingray/api/gis"

REGION_TYPE_ZIPCODE = 2
JSON_GUARD_PREFIX = "{}&&"

# Redfin's "uipt" codes for property types, mapped from our criteria strings.
UIPT_MAP = {
    "Houses": "1",
    "Condos": "2",
    "Townhomes": "3",
    "Multi-family": "4",
    "Lots": "5",
    "Manufactured": "7",
}

# Reverse direction: the integer propertyType on each home, normalized to the
# same vocabulary the Zillow provider produces so filters and the reasoner
# see one dialect.
PROPERTY_TYPE_NAMES = {
    1: "SINGLE_FAMILY",
    2: "CONDO",
    3: "TOWNHOUSE",
    4: "MULTI_FAMILY",
    5: "LOT",
    6: "OTHER",
    7: "MANUFACTURED",
    8: "COOP",
}

MAX_HOMES = 350  # Redfin's own cap per gis request
TIMEOUT_S = 20
RETRY_STATUSES = {429, 500, 502, 503, 504}


def _unwrap(text: str) -> dict[str, Any]:
    """Strip the '{}&&' guard prefix and parse the JSON that follows."""
    if text.startswith(JSON_GUARD_PREFIX):
        text = text[len(JSON_GUARD_PREFIX) :]
    return json.loads(text)


def _value(field: Any) -> Any:
    """Many Redfin fields are {'value': X, 'level': N} wrappers. Unwrap them,
    passing plain scalars through untouched."""
    if isinstance(field, dict):
        return field.get("value")
    return field


class RedfinProvider:
    name = "redfin"

    def __init__(self, session: requests.Session | None = None) -> None:
        self._session = session or requests.Session()
        self._headers = {
            # Stingray 403s non-browser agents; identify as a plain browser.
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Accept": "application/json",
        }
        # Zip -> region id, resolved once per process. Region ids are stable,
        # so re-resolving every poll would waste a request per zip.
        self._region_cache: dict[str, str] = {}

    # public ------------------------------------------------------------

    def search(self, zip_code: str, filters: HardFilters) -> Iterable[Listing]:
        region_id = self._resolve_region(zip_code)
        if region_id is None:
            log.warning("redfin: could not resolve region for zip %s", zip_code)
            return

        params = self._build_params(region_id, filters)
        data = self._request(SEARCH_PATH, params)
        homes = (data.get("payload") or {}).get("homes") or []
        for raw in homes:
            listing = self._to_listing(raw, zip_code)
            if listing is not None:
                yield listing

    # internals ---------------------------------------------------------

    def _resolve_region(self, zip_code: str) -> str | None:
        if zip_code in self._region_cache:
            return self._region_cache[zip_code]

        data = self._request(AUTOCOMPLETE_PATH, {"location": zip_code, "v": "2"})
        sections = (data.get("payload") or {}).get("sections") or []
        for section in sections:
            for row in section.get("rows") or []:
                # Row ids look like "2_18868": region type, underscore, id.
                # We only accept exact zipcode-type matches.
                row_id = str(row.get("id") or "")
                type_prefix, _, region_id = row_id.partition("_")
                if type_prefix == str(REGION_TYPE_ZIPCODE) and region_id:
                    self._region_cache[zip_code] = region_id
                    return region_id
        return None

    def _build_params(self, region_id: str, f: HardFilters) -> dict[str, Any]:
        params: dict[str, Any] = {
            "al": "1",
            "region_id": region_id,
            "region_type": str(REGION_TYPE_ZIPCODE),
            "num_homes": str(MAX_HOMES),
            "status": "9",  # active listings
            "v": "8",
        }
        if f.min_price is not None:
            params["min_price"] = f.min_price
        if f.max_price is not None:
            params["max_price"] = f.max_price
        if f.min_beds is not None:
            params["num_beds"] = int(f.min_beds)
        if f.home_types:
            mapped = [UIPT_MAP[ht] for ht in f.home_types if ht in UIPT_MAP]
            if mapped:
                params["uipt"] = ",".join(mapped)
        return params

    def _request(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{BASE_URL}{path}"
        backoff = 1.5
        for attempt in range(4):
            try:
                r = self._session.get(url, headers=self._headers, params=params, timeout=TIMEOUT_S)
            except requests.RequestException as e:
                log.warning("redfin network error on attempt %d: %s", attempt + 1, e)
                time.sleep(backoff**attempt)
                continue
            if r.status_code == 200:
                return _unwrap(r.text)
            if r.status_code in RETRY_STATUSES:
                log.warning(
                    "redfin retryable status %d on attempt %d, body=%s",
                    r.status_code,
                    attempt + 1,
                    r.text[:200],
                )
                time.sleep(backoff**attempt)
                continue
            raise RuntimeError(f"Redfin returned {r.status_code}: {r.text[:300]}")
        raise RuntimeError("Redfin: retries exhausted")

    def _to_listing(self, raw: dict[str, Any], zip_code: str) -> Listing | None:
        try:
            # Prefix the id so a Redfin home can never collide with a Zillow
            # zpid in the same store.
            property_id = raw["propertyId"]
            zpid = f"redfin:{property_id}"
            price = int(_value(raw.get("price")) or 0)
        except (KeyError, TypeError, ValueError):
            log.debug("redfin: skipping home missing propertyId/price: %s", raw)
            return None

        rel_url = str(raw.get("url") or "")
        url = f"{BASE_URL}{rel_url}" if rel_url.startswith("/") else rel_url

        street = _value(raw.get("streetLine")) or ""
        city = raw.get("city") or ""
        state = raw.get("state") or ""
        address = ", ".join(p for p in (str(street), str(city), str(state)) if p)

        sqft = _value(raw.get("sqFt"))
        lot_sqft = _value(raw.get("lotSize"))
        dom = _value(raw.get("dom"))
        lat_long = _value(raw.get("latLong")) or {}

        prop_type = raw.get("propertyType")
        home_type = PROPERTY_TYPE_NAMES.get(prop_type) if isinstance(prop_type, int) else None

        return Listing(
            zpid=zpid,
            zip_code=str(zip_code),
            price=price,
            beds=float(raw.get("beds") or 0),
            baths=float(raw.get("baths") or 0),
            sqft=int(sqft) if sqft else None,
            status="FOR_SALE",  # we only request active listings (status=9)
            home_type=home_type,
            url=url or f"{BASE_URL}/home/{property_id}",
            address=address,
            days_on_market=int(dom) if dom is not None else None,
            image_url=None,  # photo URLs need a separate authenticated CDN scheme
            latitude=lat_long.get("latitude") if isinstance(lat_long, dict) else None,
            longitude=lat_long.get("longitude") if isinstance(lat_long, dict) else None,
            lot_sqft=int(lot_sqft) if lot_sqft else None,
            description=None,
            raw=raw,
        )
