from pathlib import Path

from listing_agent.models import Listing
from listing_agent.store import Store


def make_listing(zpid: str = "1", price: int = 500_000) -> Listing:
    return Listing(
        zpid=zpid,
        zip_code="78704",
        price=price,
        beds=3,
        baths=2,
        sqft=1600,
        status="FOR_SALE",
        home_type="SINGLE_FAMILY",
        url=f"https://example.com/{zpid}",
        address="1 Test St",
    )


# upsert() used to return a bare bool; it now returns UpsertResult so the
# pipeline can see the previous price for drop detection.
def test_upsert_new_then_existing(tmp_path: Path):
    s = Store(tmp_path / "t.db")
    first = s.upsert(make_listing("1"))
    assert first.is_new is True
    assert first.old_price is None
    second = s.upsert(make_listing("1"))
    assert second.is_new is False
    assert second.old_price == 500_000


def test_upsert_reports_price_before_update(tmp_path: Path):
    s = Store(tmp_path / "t.db")
    s.upsert(make_listing("1", price=500_000))
    result = s.upsert(make_listing("1", price=480_000))
    # old_price is the pre-update value; the row itself now holds the new one.
    assert result.old_price == 500_000
    assert s.get("1")["price"] == 480_000


def test_price_history_records_transitions_not_polls(tmp_path: Path):
    s = Store(tmp_path / "t.db")
    s.upsert(make_listing("1", price=500_000))
    s.upsert(make_listing("1", price=500_000))  # same price: no new row
    s.upsert(make_listing("1", price=480_000))
    prices = [row["price"] for row in s.price_history("1")]
    assert prices == [500_000, 480_000]


def test_notification_idempotent(tmp_path: Path):
    s = Store(tmp_path / "t.db")
    s.upsert(make_listing("1"))
    assert s.already_notified("1", "discord") is False
    s.mark_notified("1", "discord", 88, "MATCH")
    assert s.already_notified("1", "discord") is True
    # Re-marking is a no-op (PK conflict ignored).
    s.mark_notified("1", "discord", 99, "STRONG_MATCH")


def test_run_context_records_stats(tmp_path: Path):
    s = Store(tmp_path / "t.db")
    with s.run() as stats:
        stats["fetched"] = 5
        stats["new_count"] = 2
        stats["notified"] = 1
    # No assertion on row count; smoke test that nothing throws.
