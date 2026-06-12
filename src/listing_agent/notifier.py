"""Notification sinks.

Discord first because the webhook is the lowest-friction option. Add Slack
or email by writing another `send_*` function with the same signature, then
returning the right sink from `make_notifier`.

Every sink returns a `channel` string that the store uses as part of the
idempotency key. Keep these stable. If you rename a channel you will
re-notify everything once.
"""

from __future__ import annotations

import logging
from typing import Protocol

import requests

from .models import Listing, ReasonerVerdict

log = logging.getLogger(__name__)


class Notifier(Protocol):
    channel: str

    def send(self, listing: Listing, verdict: ReasonerVerdict) -> bool:
        """Return True on success. Failures should log and return False so the
        run can continue."""
        ...


# ----- console sink (default if no webhook configured) ----------------


class ConsoleNotifier:
    channel = "console"

    def send(self, listing: Listing, verdict: ReasonerVerdict) -> bool:
        print(
            f"[{verdict.verdict} {verdict.score}] {listing.address} "
            f"${listing.price:,} {listing.beds}bd/{listing.baths}ba"
        )
        print(f"  {verdict.summary}")
        if verdict.highlights:
            print("  + " + " | ".join(verdict.highlights))
        if verdict.red_flags:
            print("  ! " + " | ".join(verdict.red_flags))
        print(f"  {listing.url}")
        return True


# ----- discord sink ---------------------------------------------------


class DiscordNotifier:
    channel = "discord"

    def __init__(self, webhook_url: str) -> None:
        if not webhook_url:
            raise ValueError("Discord webhook URL is empty")
        self._url = webhook_url

    def send(self, listing: Listing, verdict: ReasonerVerdict) -> bool:
        color = {
            "STRONG_MATCH": 0x2ECC71,
            "MATCH": 0x3498DB,
            "SOFT_PASS": 0xF1C40F,
            "REJECT": 0xE74C3C,
        }.get(verdict.verdict, 0x95A5A6)

        fields = [
            {"name": "Price", "value": f"${listing.price:,}", "inline": True},
            {"name": "Beds/Baths", "value": f"{listing.beds}/{listing.baths}", "inline": True},
            {"name": "Sqft", "value": str(listing.sqft or "n/a"), "inline": True},
            {"name": "Score", "value": f"{verdict.score} ({verdict.verdict})", "inline": True},
        ]
        if verdict.highlights:
            fields.append(
                {
                    "name": "Highlights",
                    "value": "\n".join(f"+ {h}" for h in verdict.highlights),
                    "inline": False,
                }
            )
        if verdict.red_flags:
            fields.append(
                {
                    "name": "Red flags",
                    "value": "\n".join(f"! {r}" for r in verdict.red_flags),
                    "inline": False,
                }
            )
        if verdict.needs_human:
            fields.append(
                {
                    "name": "Note",
                    "value": "Listing data is thin. Worth a manual look.",
                    "inline": False,
                }
            )

        embed = {
            "title": listing.address or f"Listing {listing.zpid}",
            "url": listing.url,
            "description": verdict.summary,
            "color": color,
            "fields": fields,
        }
        if listing.image_url:
            embed["thumbnail"] = {"url": listing.image_url}

        payload = {"username": "Listing Agent", "embeds": [embed]}
        try:
            r = requests.post(self._url, json=payload, timeout=10)
        except requests.RequestException as e:
            log.warning("discord webhook failed: %s", e)
            return False
        if r.status_code >= 300:
            log.warning("discord webhook %d: %s", r.status_code, r.text[:200])
            return False
        return True


# ----- factory --------------------------------------------------------


def make_notifier(discord_webhook_url: str | None) -> Notifier:
    if discord_webhook_url:
        return DiscordNotifier(discord_webhook_url)
    return ConsoleNotifier()
