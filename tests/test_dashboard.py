"""Dashboard rendering tests.

render_page is a pure function from Store to HTML string, so no HTTP server
is spun up here; serve() is a thin stdlib wrapper around it.
"""

from pathlib import Path

from listing_agent.dashboard import render_page
from listing_agent.models import Listing
from listing_agent.store import Store


def make_listing(zpid: str = "1", price: int = 500_000, **over) -> Listing:
    base = dict(
        zpid=zpid,
        zip_code="78704",
        price=price,
        beds=3.0,
        baths=2.0,
        sqft=1600,
        status="FOR_SALE",
        home_type="SINGLE_FAMILY",
        url=f"https://example.com/{zpid}",
        address=f"{zpid} Test St",
    )
    base.update(over)
    return Listing(**base)


def test_empty_store_renders_placeholders(tmp_path: Path):
    html = render_page(Store(tmp_path / "t.db"))
    assert "No listings yet." in html
    assert "No notifications yet." in html
    assert "No runs yet." in html


def test_listing_and_notification_appear(tmp_path: Path):
    store = Store(tmp_path / "t.db")
    store.upsert(make_listing("1"))
    store.mark_notified("1", "discord", 88, "MATCH")

    html = render_page(store)
    assert "1 Test St" in html
    assert "$500,000" in html
    assert 'href="https://example.com/1"' in html
    assert "MATCH" in html


def test_price_change_count_reflects_history(tmp_path: Path):
    store = Store(tmp_path / "t.db")
    store.upsert(make_listing("1", price=500_000))
    store.upsert(make_listing("1", price=480_000))

    counts = store.counts()
    assert counts["price_changes"] == 1
    assert counts["listings"] == 1


def test_html_is_escaped(tmp_path: Path):
    store = Store(tmp_path / "t.db")
    store.upsert(make_listing("1", address='<script>alert("x")</script>'))

    html = render_page(store)
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html
