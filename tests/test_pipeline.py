"""End-to-end pipeline tests with fake provider/reasoner/notifier.

This is the offline equivalent of the stage-2 dry run: it proves the
orchestration (dedup, filters, cap, idempotent notify) without any network.
The fakes satisfy the Provider/Notifier Protocols structurally; no
inheritance needed.
"""

from pathlib import Path

import pytest

from listing_agent.config import AppConfig, HardFilters, Preferences, ReasonerConfig
from listing_agent.models import Listing, ReasonerVerdict
from listing_agent.pipeline import run_once
from listing_agent.store import Store


def make_listing(zpid: str, **over) -> Listing:
    base = dict(
        zpid=zpid,
        zip_code="78704",
        price=500_000,
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


class FakeProvider:
    name = "fake"

    def __init__(self, listings):
        self.listings = listings

    def search(self, zip_code, filters):
        yield from self.listings


class FakeReasoner:
    def __init__(self, score=90):
        self.calls = []
        self._score = score

    def score(self, listing, prefs):
        self.calls.append(listing.zpid)
        return ReasonerVerdict(
            score=self._score,
            verdict="STRONG_MATCH" if self._score >= 90 else "SOFT_PASS",
            summary="test",
            highlights=[],
            red_flags=[],
            needs_human=False,
        )


class FakeNotifier:
    channel = "fake"

    def __init__(self, succeed=True):
        self.sent = []
        self.drops = []
        self._succeed = succeed

    def send(self, listing, verdict):
        self.sent.append((listing.zpid, verdict.score))
        return self._succeed

    def send_price_drop(self, listing, old_price):
        self.drops.append((listing.zpid, old_price, listing.price))
        return self._succeed


def make_cfg(tmp_path: Path, **over) -> AppConfig:
    base = dict(
        rapidapi_key="x",
        anthropic_api_key="x",
        discord_webhook_url=None,
        db_path=tmp_path / "seen.db",
        max_reasoner_calls=25,
        zip_codes=["78704"],
        filters=HardFilters(),
        preferences=Preferences(),
        reasoner=ReasonerConfig(min_score_to_notify=70),
    )
    base.update(over)
    return AppConfig(**base)


@pytest.fixture
def store(tmp_path) -> Store:
    return Store(tmp_path / "seen.db")


def test_new_listing_is_scored_and_notified(tmp_path, store):
    cfg = make_cfg(tmp_path)
    reasoner, notifier = FakeReasoner(score=90), FakeNotifier()

    run_once(cfg, FakeProvider([make_listing("1")]), store, reasoner, notifier)

    assert reasoner.calls == ["1"]
    assert notifier.sent == [("1", 90)]
    assert store.already_notified("1", "fake")


def test_second_run_does_not_renotify(tmp_path, store):
    cfg = make_cfg(tmp_path)
    provider = FakeProvider([make_listing("1")])

    run_once(cfg, provider, store, FakeReasoner(), FakeNotifier())
    second_reasoner, second_notifier = FakeReasoner(), FakeNotifier()
    run_once(cfg, provider, store, second_reasoner, second_notifier)

    # The listing is no longer "new", so it never reaches the reasoner.
    assert second_reasoner.calls == []
    assert second_notifier.sent == []


def test_filtered_listing_never_reaches_reasoner(tmp_path, store):
    cfg = make_cfg(tmp_path, filters=HardFilters(max_price=400_000))
    reasoner, notifier = FakeReasoner(), FakeNotifier()

    run_once(cfg, FakeProvider([make_listing("1", price=500_000)]), store, reasoner, notifier)

    assert reasoner.calls == []
    assert notifier.sent == []


def test_dry_run_skips_reasoner_and_notifier(tmp_path, store):
    cfg = make_cfg(tmp_path)
    reasoner, notifier = FakeReasoner(), FakeNotifier()

    run_once(cfg, FakeProvider([make_listing("1")]), store, reasoner, notifier, dry_run=True)

    assert reasoner.calls == []
    assert notifier.sent == []
    # The listing is still recorded so the next full run treats it as seen.
    assert store.get("1") is not None


def test_reasoner_cap_defers_remaining_listings(tmp_path, store):
    cfg = make_cfg(tmp_path, max_reasoner_calls=1)
    reasoner, notifier = FakeReasoner(score=90), FakeNotifier()
    listings = [make_listing("1"), make_listing("2")]

    run_once(cfg, FakeProvider(listings), store, reasoner, notifier)

    assert reasoner.calls == ["1"]
    # Listing 2 must NOT be in the store: an upserted listing would read as
    # "seen" next run and never get scored. Staying unrecorded is what makes
    # the deferral real.
    assert store.get("2") is None

    # Next run picks up the deferred listing.
    next_reasoner, next_notifier = FakeReasoner(score=90), FakeNotifier()
    run_once(cfg, FakeProvider(listings), store, next_reasoner, next_notifier)
    assert next_reasoner.calls == ["2"]


def test_low_score_is_not_notified(tmp_path, store):
    cfg = make_cfg(tmp_path)
    reasoner, notifier = FakeReasoner(score=40), FakeNotifier()

    run_once(cfg, FakeProvider([make_listing("1")]), store, reasoner, notifier)

    assert reasoner.calls == ["1"]
    assert notifier.sent == []


def test_failed_notify_is_not_marked_sent(tmp_path, store):
    cfg = make_cfg(tmp_path)
    notifier = FakeNotifier(succeed=False)

    run_once(cfg, FakeProvider([make_listing("1")]), store, FakeReasoner(score=90), notifier)

    # Send failed, so the idempotency row must NOT exist; a later run with a
    # working sink would still be blocked by dedup (see test above), but the
    # store stays truthful about what was actually delivered.
    assert not store.already_notified("1", "fake")


# ----- price drops -----------------------------------------------------


def test_price_drop_notifies_once(tmp_path, store):
    cfg = make_cfg(tmp_path)
    run_once(
        cfg, FakeProvider([make_listing("1", price=500_000)]), store, FakeReasoner(), FakeNotifier()
    )

    # Second run: same listing, 4% cheaper. Known listing, so no reasoner
    # call, but the drop clears the 3% default and pings.
    notifier = FakeNotifier()
    run_once(cfg, FakeProvider([make_listing("1", price=480_000)]), store, FakeReasoner(), notifier)
    assert notifier.drops == [("1", 500_000, 480_000)]

    # Third run at the unchanged new price: the transition already happened,
    # so nothing fires. This is the dedup mechanism for drops.
    notifier3 = FakeNotifier()
    run_once(
        cfg, FakeProvider([make_listing("1", price=480_000)]), store, FakeReasoner(), notifier3
    )
    assert notifier3.drops == []


def test_small_price_drop_is_ignored(tmp_path, store):
    cfg = make_cfg(tmp_path)
    run_once(
        cfg, FakeProvider([make_listing("1", price=500_000)]), store, FakeReasoner(), FakeNotifier()
    )

    notifier = FakeNotifier()
    # 1% drop, under the 3% default threshold.
    run_once(cfg, FakeProvider([make_listing("1", price=495_000)]), store, FakeReasoner(), notifier)
    assert notifier.drops == []


def test_price_drop_into_budget_notifies(tmp_path, store):
    # 800k listing fails max_price=750k on first sight, then drops to 740k.
    # Filters run against the new price, so this drop is exactly the ping we
    # want: the listing just became affordable.
    cfg = make_cfg(tmp_path, filters=HardFilters(max_price=750_000))
    run_once(
        cfg, FakeProvider([make_listing("1", price=800_000)]), store, FakeReasoner(), FakeNotifier()
    )

    notifier = FakeNotifier()
    run_once(cfg, FakeProvider([make_listing("1", price=740_000)]), store, FakeReasoner(), notifier)
    assert notifier.drops == [("1", 800_000, 740_000)]


def test_price_drop_still_over_budget_is_silent(tmp_path, store):
    cfg = make_cfg(tmp_path, filters=HardFilters(max_price=750_000))
    run_once(
        cfg, FakeProvider([make_listing("1", price=900_000)]), store, FakeReasoner(), FakeNotifier()
    )

    notifier = FakeNotifier()
    run_once(cfg, FakeProvider([make_listing("1", price=850_000)]), store, FakeReasoner(), notifier)
    assert notifier.drops == []


def test_price_drop_respects_disabled_config(tmp_path, store):
    from listing_agent.config import PriceDropConfig

    cfg = make_cfg(tmp_path, price_drops=PriceDropConfig(enabled=False))
    run_once(
        cfg, FakeProvider([make_listing("1", price=500_000)]), store, FakeReasoner(), FakeNotifier()
    )

    notifier = FakeNotifier()
    run_once(cfg, FakeProvider([make_listing("1", price=400_000)]), store, FakeReasoner(), notifier)
    assert notifier.drops == []


def test_price_drop_dry_run_does_not_send(tmp_path, store):
    cfg = make_cfg(tmp_path)
    run_once(
        cfg, FakeProvider([make_listing("1", price=500_000)]), store, FakeReasoner(), FakeNotifier()
    )

    notifier = FakeNotifier()
    run_once(
        cfg,
        FakeProvider([make_listing("1", price=450_000)]),
        store,
        FakeReasoner(),
        notifier,
        dry_run=True,
    )
    assert notifier.drops == []
