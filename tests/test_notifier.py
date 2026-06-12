"""Notifier tests. Discord calls are mocked; nothing leaves the machine."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
import requests

from listing_agent.models import Listing, ReasonerVerdict
from listing_agent.notifier import ConsoleNotifier, DiscordNotifier, make_notifier


def make_listing(**over) -> Listing:
    base = dict(
        zpid="1",
        zip_code="78704",
        price=500_000,
        beds=3.0,
        baths=2.0,
        sqft=1600,
        status="FOR_SALE",
        home_type="SINGLE_FAMILY",
        url="https://example.com/1",
        address="1 Test St",
        image_url="https://example.com/1.jpg",
    )
    base.update(over)
    return Listing(**base)


def make_verdict(**over) -> ReasonerVerdict:
    base = dict(
        score=88,
        verdict="MATCH",
        summary="Good fit.",
        highlights=["garage"],
        red_flags=["busy street"],
        needs_human=False,
    )
    base.update(over)
    return ReasonerVerdict(**base)


# ----- console ---------------------------------------------------------


def test_console_prints_verdict_and_url(capsys):
    ok = ConsoleNotifier().send(make_listing(), make_verdict())
    out = capsys.readouterr().out
    assert ok is True
    assert "MATCH 88" in out
    assert "1 Test St" in out
    assert "https://example.com/1" in out


# ----- discord ---------------------------------------------------------


def test_discord_rejects_empty_url():
    with pytest.raises(ValueError):
        DiscordNotifier("")


def test_discord_sends_embed_payload():
    with patch("listing_agent.notifier.requests.post") as post:
        post.return_value = SimpleNamespace(status_code=204, text="")
        ok = DiscordNotifier("https://discord.test/hook").send(make_listing(), make_verdict())

    assert ok is True
    payload = post.call_args.kwargs["json"]
    embed = payload["embeds"][0]
    assert embed["title"] == "1 Test St"
    assert embed["url"] == "https://example.com/1"
    assert embed["thumbnail"] == {"url": "https://example.com/1.jpg"}
    field_names = [f["name"] for f in embed["fields"]]
    assert "Price" in field_names
    assert "Red flags" in field_names


def test_discord_flags_needs_human():
    with patch("listing_agent.notifier.requests.post") as post:
        post.return_value = SimpleNamespace(status_code=204, text="")
        DiscordNotifier("https://discord.test/hook").send(
            make_listing(), make_verdict(needs_human=True)
        )
    fields = post.call_args.kwargs["json"]["embeds"][0]["fields"]
    assert any(f["name"] == "Note" for f in fields)


def test_discord_returns_false_on_http_error():
    with patch("listing_agent.notifier.requests.post") as post:
        post.return_value = SimpleNamespace(status_code=400, text="bad request")
        ok = DiscordNotifier("https://discord.test/hook").send(make_listing(), make_verdict())
    assert ok is False


def test_discord_returns_false_on_network_error():
    with patch("listing_agent.notifier.requests.post") as post:
        post.side_effect = requests.ConnectionError("boom")
        ok = DiscordNotifier("https://discord.test/hook").send(make_listing(), make_verdict())
    assert ok is False


# ----- factory ---------------------------------------------------------


def test_factory_picks_discord_when_url_present():
    n = make_notifier("https://discord.test/hook")
    assert isinstance(n, DiscordNotifier)
    assert n.channel == "discord"


def test_factory_falls_back_to_console():
    n = make_notifier(None)
    assert isinstance(n, ConsoleNotifier)
    assert n.channel == "console"
