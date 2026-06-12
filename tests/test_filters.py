from listing_agent.config import HardFilters
from listing_agent.filters import check
from listing_agent.models import Listing


def make_listing(**over) -> Listing:
    base = dict(
        zpid="1",
        zip_code="78704",
        price=500_000,
        beds=3,
        baths=2,
        sqft=1600,
        status="FOR_SALE",
        home_type="SINGLE_FAMILY",
        url="https://example.com/1",
        address="1 Test St",
    )
    base.update(over)
    return Listing(**base)


def test_passes_when_no_filters_set():
    assert check(make_listing(), HardFilters()).passed


def test_rejects_above_max_price():
    res = check(make_listing(price=800_000), HardFilters(max_price=750_000))
    assert not res.passed
    assert any("price" in r for r in res.reasons)


def test_unknown_sqft_does_not_fail_min_sqft():
    res = check(make_listing(sqft=None), HardFilters(min_sqft=1000))
    assert res.passed


def test_excluded_status_fails():
    res = check(make_listing(status="PENDING"), HardFilters(exclude_statuses=["PENDING"]))
    assert not res.passed
