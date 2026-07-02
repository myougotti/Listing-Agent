"""Redfin provider parsing tests, no network.

The fixture mirrors the shape of Redfin's stingray gis payload, including
the '{}&&' guard prefix on the wire. Like the Zillow tests, a failure here
is the fastest way to localize a schema change by the source.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

from listing_agent.config import HardFilters
from listing_agent.providers.redfin import RedfinProvider, _unwrap

FIXTURE = Path(__file__).parent / "fixtures" / "redfin_home.json"

AUTOCOMPLETE_BODY = "{}&&" + json.dumps(
    {
        "payload": {
            "sections": [
                {
                    "rows": [
                        {"id": "6_11619", "name": "Austin, TX", "type": "6"},
                        {"id": "2_18868", "name": "78704", "type": "2"},
                    ]
                }
            ]
        }
    }
)


def _mock_response(text: str, status: int = 200):
    r = MagicMock()
    r.status_code = status
    r.text = text
    return r


def _session_for(homes: list[dict]) -> MagicMock:
    """First GET resolves the region, second returns the homes payload."""
    search_body = "{}&&" + json.dumps({"payload": {"homes": homes}})
    session = MagicMock()
    session.get.side_effect = [
        _mock_response(AUTOCOMPLETE_BODY),
        _mock_response(search_body),
    ]
    return session


def test_unwrap_strips_guard_prefix():
    assert _unwrap('{}&&{"a": 1}') == {"a": 1}
    # Plain JSON without the guard also parses.
    assert _unwrap('{"a": 1}') == {"a": 1}


def test_parses_sample_home():
    raw = json.loads(FIXTURE.read_text())
    p = RedfinProvider(session=_session_for([raw]))
    results = list(p.search("78704", HardFilters()))

    assert len(results) == 1
    listing = results[0]
    # Prefixed id keeps Redfin homes from colliding with Zillow zpids.
    assert listing.zpid == "redfin:123456789"
    assert listing.price == 525_000
    assert listing.beds == 3
    assert listing.sqft == 1620
    assert listing.lot_sqft == 6500
    assert listing.address == "4501 Sunset Trl, Austin, TX"
    assert listing.url == "https://www.redfin.com/TX/Austin/4501-Sunset-Trl-78704/home/123456789"
    assert listing.days_on_market == 5
    assert listing.latitude == 30.2451


def test_region_is_resolved_once_per_zip():
    raw = json.loads(FIXTURE.read_text())
    search_body = "{}&&" + json.dumps({"payload": {"homes": [raw]}})
    session = MagicMock()
    session.get.side_effect = [
        _mock_response(AUTOCOMPLETE_BODY),
        _mock_response(search_body),
        _mock_response(search_body),  # second search reuses the cached region
    ]

    p = RedfinProvider(session=session)
    list(p.search("78704", HardFilters()))
    list(p.search("78704", HardFilters()))

    autocomplete_calls = [
        c for c in session.get.call_args_list if "location-autocomplete" in c.args[0]
    ]
    assert len(autocomplete_calls) == 1


def test_unresolvable_zip_yields_nothing():
    empty = "{}&&" + json.dumps({"payload": {"sections": []}})
    session = MagicMock()
    session.get.return_value = _mock_response(empty)

    p = RedfinProvider(session=session)
    assert list(p.search("00000", HardFilters())) == []


def test_filters_are_pushed_to_params():
    raw = json.loads(FIXTURE.read_text())
    session = _session_for([raw])
    p = RedfinProvider(session=session)
    filters = HardFilters(min_price=250_000, max_price=750_000, min_beds=2, home_types=["Houses"])
    list(p.search("78704", filters))

    params = session.get.call_args_list[1].kwargs["params"]
    assert params["min_price"] == 250_000
    assert params["max_price"] == 750_000
    assert params["num_beds"] == 2
    assert params["uipt"] == "1"


def test_home_missing_property_id_is_skipped():
    p = RedfinProvider(session=_session_for([{"price": {"value": 100}}]))
    assert list(p.search("78704", HardFilters())) == []
