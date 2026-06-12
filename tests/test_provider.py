"""Tests the provider's parsing without touching the network.

If the real API response shape changes, the failing assertion here is the
fastest way to localize the break.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from listing_agent.config import HardFilters
from listing_agent.providers.zillow_rapidapi import ZillowRapidAPIProvider

FIXTURE = Path(__file__).parent / "fixtures" / "sample_listing.json"


def _mock_response(payload, status=200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload
    r.text = json.dumps(payload)
    return r


def test_parses_sample_listing():
    raw = json.loads(FIXTURE.read_text())
    payload = {"props": [raw], "totalPages": 1}

    session = MagicMock()
    session.get.return_value = _mock_response(payload)

    p = ZillowRapidAPIProvider(api_key="test-key", session=session)
    results = list(p.search("78704", HardFilters()))

    assert len(results) == 1
    listing = results[0]
    assert listing.zpid == "29381838"
    assert listing.price == 525_000
    assert listing.beds == 3
    assert listing.sqft == 1620
    assert listing.url.startswith("https://www.zillow.com/homedetails/")
    assert listing.lot_sqft == 6500


def test_empty_props_yields_nothing():
    session = MagicMock()
    session.get.return_value = _mock_response({"props": [], "totalPages": 1})
    p = ZillowRapidAPIProvider(api_key="test-key", session=session)
    assert list(p.search("00000", HardFilters())) == []


def test_missing_key_raises():
    with pytest.raises(ValueError):
        ZillowRapidAPIProvider(api_key="")
