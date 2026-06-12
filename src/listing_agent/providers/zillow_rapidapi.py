"""RapidAPI 'zillow-com1' provider.

Endpoint reference (subject to change by the publisher):
  GET https://zillow-com1.p.rapidapi.com/propertyExtendedSearch

Query params we use:
  location       (str)  zip code, city, or full address
  status_type    (str)  ForSale | ForRent | RecentlySold
  home_type      (str)  comma-joined: Houses,Townhomes,Multi-family,
                        Apartments_Condos_Co-ops,Manufactured,LotsLand
  minPrice       (int)
  maxPrice       (int)
  bedsMin        (int)
  bathsMin       (int)
  page           (int)  pagination
  sort           (str)  Newest | Homes_for_You | Price_High_Low | Price_Low_High

Response shape (relevant fields):
  {
    "props": [
      {
        "zpid": "12345",
        "address": "...",
        "price": 500000,
        "bedrooms": 3,
        "bathrooms": 2,
        "livingArea": 1800,
        "imgSrc": "...",
        "listingStatus": "FOR_SALE",
        "detailUrl": "/homedetails/.../12345_zpid/",
        "propertyType": "SINGLE_FAMILY",
        "latitude": 30.25,
        "longitude": -97.74,
        "lotAreaValue": 5000,
        "lotAreaUnit": "sqft",
        "daysOnZillow": 5
      }
    ],
    "totalPages": 5
  }

If the publisher changes their schema, only this file should need edits.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from typing import Any

import requests

from ..config import HardFilters
from ..models import Listing

log = logging.getLogger(__name__)

BASE_URL = "https://zillow-com1.p.rapidapi.com"
HOST = "zillow-com1.p.rapidapi.com"
SEARCH_PATH = "/propertyExtendedSearch"

# Map the criteria home_type strings to the values the API expects.
# Adjust if you see the publisher use different tokens.
HOME_TYPE_MAP = {
    "Houses": "Houses",
    "Townhomes": "Townhomes",
    "Condos": "Apartments_Condos_Co-ops",
    "Multi-family": "Multi-family",
    "Manufactured": "Manufactured",
    "Lots": "LotsLand",
}

MAX_PAGES = 3  # cap pagination to control quota
TIMEOUT_S = 20
RETRY_STATUSES = {429, 500, 502, 503, 504}


class ZillowRapidAPIProvider:
    name = "zillow_rapidapi"

    def __init__(self, api_key: str, session: requests.Session | None = None) -> None:
        if not api_key:
            raise ValueError("RAPIDAPI_KEY is empty")
        self._headers = {
            "X-RapidAPI-Key": api_key,
            "X-RapidAPI-Host": HOST,
            "Accept": "application/json",
        }
        self._session = session or requests.Session()

    # public ------------------------------------------------------------

    def search(self, zip_code: str, filters: HardFilters) -> Iterable[Listing]:
        params = self._build_params(zip_code, filters)
        for page in range(1, MAX_PAGES + 1):
            params["page"] = page
            data = self._request(params)
            props = data.get("props") or []
            if not props:
                return
            for raw in props:
                listing = self._to_listing(raw, zip_code)
                if listing is not None:
                    yield listing
            if page >= int(data.get("totalPages") or 1):
                return

    # internals ---------------------------------------------------------

    def _build_params(self, zip_code: str, f: HardFilters) -> dict[str, Any]:
        params: dict[str, Any] = {
            "location": zip_code,
            "status_type": "ForSale",
            "sort": "Newest",
        }
        if f.min_price is not None:
            params["minPrice"] = f.min_price
        if f.max_price is not None:
            params["maxPrice"] = f.max_price
        if f.min_beds is not None:
            params["bedsMin"] = int(f.min_beds)
        if f.min_baths is not None:
            params["bathsMin"] = int(f.min_baths)
        if f.home_types:
            mapped = [HOME_TYPE_MAP.get(ht, ht) for ht in f.home_types]
            params["home_type"] = ",".join(mapped)
        return params

    def _request(self, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{BASE_URL}{SEARCH_PATH}"
        backoff = 1.5
        for attempt in range(4):
            try:
                r = self._session.get(url, headers=self._headers, params=params, timeout=TIMEOUT_S)
            except requests.RequestException as e:
                log.warning("network error on attempt %d: %s", attempt + 1, e)
                time.sleep(backoff**attempt)
                continue
            if r.status_code == 200:
                return r.json()
            if r.status_code in RETRY_STATUSES:
                log.warning(
                    "retryable status %d on attempt %d, body=%s",
                    r.status_code,
                    attempt + 1,
                    r.text[:200],
                )
                time.sleep(backoff**attempt)
                continue
            # Non-retryable. Surface useful info.
            raise RuntimeError(f"Zillow RapidAPI returned {r.status_code}: {r.text[:300]}")
        raise RuntimeError("Zillow RapidAPI: retries exhausted")

    def _to_listing(self, raw: dict[str, Any], zip_code: str) -> Listing | None:
        try:
            zpid = str(raw["zpid"])
            price = int(raw["price"]) if raw.get("price") is not None else 0
        except (KeyError, TypeError, ValueError):
            log.debug("skipping prop missing zpid/price: %s", raw)
            return None

        detail_url = raw.get("detailUrl") or ""
        if detail_url.startswith("/"):
            url = f"https://www.zillow.com{detail_url}"
        else:
            url = detail_url or f"https://www.zillow.com/homedetails/{zpid}_zpid/"

        lot_sqft = None
        if raw.get("lotAreaUnit") == "sqft" and raw.get("lotAreaValue") is not None:
            try:
                lot_sqft = int(raw["lotAreaValue"])
            except (TypeError, ValueError):
                lot_sqft = None

        return Listing(
            zpid=zpid,
            zip_code=str(zip_code),
            price=price,
            beds=float(raw.get("bedrooms") or 0),
            baths=float(raw.get("bathrooms") or 0),
            sqft=int(raw["livingArea"]) if raw.get("livingArea") else None,
            status=str(raw.get("listingStatus") or "UNKNOWN"),
            home_type=raw.get("propertyType"),
            url=url,
            address=str(raw.get("address") or ""),
            days_on_market=raw.get("daysOnZillow"),
            image_url=raw.get("imgSrc"),
            latitude=raw.get("latitude"),
            longitude=raw.get("longitude"),
            lot_sqft=lot_sqft,
            description=None,  # search endpoint does not include it
            raw=raw,
        )
