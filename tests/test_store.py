from pathlib import Path

from listing_agent.models import Listing
from listing_agent.store import Store


def make_listing(zpid: str = "1") -> Listing:
    return Listing(
        zpid=zpid,
        zip_code="78704",
        price=500_000,
        beds=3,
        baths=2,
        sqft=1600,
        status="FOR_SALE",
        home_type="SINGLE_FAMILY",
        url=f"https://example.com/{zpid}",
        address="1 Test St",
    )


def test_upsert_new_then_existing(tmp_path: Path):
    s = Store(tmp_path / "t.db")
    assert s.upsert(make_listing("1")) is True
    assert s.upsert(make_listing("1")) is False


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
