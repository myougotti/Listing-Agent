"""Claude-based listing scorer.

Design choices worth understanding before you edit:

1. Output is forced via tool_use, not free-form JSON. We declare one tool with
   the exact schema we want, set tool_choice to require it, and read the
   tool_input. This eliminates "the model wrapped its JSON in markdown" bugs.

2. The system prompt is short and concrete. The criteria are passed as data
   in the user turn, not baked into the system prompt, so changes to your
   YAML do not invalidate any prompt cache.

3. We never give the model the raw provider blob. We give it a small,
   shaped fact sheet. Less noise to misinterpret.

4. The reasoner can return needs_human=true. Treat low confidence as a
   signal, not noise. Surface those in the notification.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from anthropic import Anthropic

from .config import Preferences, ReasonerConfig
from .models import Listing, ReasonerVerdict

log = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are a real estate listing analyst helping a buyer triage \
new listings against their criteria.

Your job:
1. Read the listing fact sheet and the buyer's preferences.
2. Decide how well this listing fits, on a 0 to 100 scale.
3. Surface concrete highlights and red flags grounded in the listing data. \
Do not invent facts. If a relevant field is missing, say so rather than guess.
4. Call the report_listing_assessment tool with your verdict. Do not reply in \
plain text. Always call the tool exactly once.

Scoring guide:
  90-100  STRONG_MATCH   hits must-haves, no red flags, several nice-to-haves
  70-89   MATCH          hits must-haves, minor concerns or missing info
  50-69   SOFT_PASS      partial fit, notable gaps, worth a quick look
  0-49    REJECT         fails a must-have or hits a deal-breaker

Set needs_human=true when the listing data is too thin to score with \
confidence, OR when a deal-breaker is plausible but unconfirmed from the data."""


SCORING_TOOL: dict[str, Any] = {
    "name": "report_listing_assessment",
    "description": "Report your structured assessment of the listing.",
    "input_schema": {
        "type": "object",
        "properties": {
            "score": {
                "type": "integer",
                "minimum": 0,
                "maximum": 100,
                "description": "Overall fit, 0 (terrible) to 100 (ideal).",
            },
            "verdict": {
                "type": "string",
                "enum": ["STRONG_MATCH", "MATCH", "SOFT_PASS", "REJECT"],
            },
            "summary": {
                "type": "string",
                "description": "One sentence, 30 words or fewer, plain English.",
            },
            "highlights": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 4,
                "description": "Concrete positives tied to listing fields.",
            },
            "red_flags": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 4,
                "description": "Concrete concerns tied to listing fields. Be specific.",
            },
            "needs_human": {
                "type": "boolean",
                "description": "True if data is too ambiguous to score confidently.",
            },
        },
        "required": ["score", "verdict", "summary", "highlights", "red_flags", "needs_human"],
    },
}


def _fact_sheet(listing: Listing) -> str:
    """Render a compact, model-readable view of the listing."""
    dom = listing.days_on_market if listing.days_on_market is not None else "unknown"
    lines = [
        f"address: {listing.address}",
        f"zip: {listing.zip_code}",
        f"price_usd: {listing.price}",
        f"beds: {listing.beds}",
        f"baths: {listing.baths}",
        f"sqft: {listing.sqft if listing.sqft is not None else 'unknown'}",
        f"lot_sqft: {listing.lot_sqft if listing.lot_sqft is not None else 'unknown'}",
        f"home_type: {listing.home_type or 'unknown'}",
        f"status: {listing.status}",
        f"days_on_market: {dom}",
        f"url: {listing.url}",
    ]
    if listing.description:
        lines.append(f"description: {listing.description}")
    return "\n".join(lines)


def _build_user_message(listing: Listing, prefs: Preferences) -> str:
    parts = [
        "LISTING FACT SHEET:",
        _fact_sheet(listing),
        "",
        "BUYER PREFERENCES:",
        "must_have: " + (json.dumps(prefs.must_have) if prefs.must_have else "[]"),
        "nice_to_have: " + (json.dumps(prefs.nice_to_have) if prefs.nice_to_have else "[]"),
        "deal_breakers: " + (json.dumps(prefs.deal_breakers) if prefs.deal_breakers else "[]"),
        "",
        "Score this listing now by calling report_listing_assessment.",
    ]
    return "\n".join(parts)


class Reasoner:
    def __init__(self, api_key: str, cfg: ReasonerConfig) -> None:
        self._client = Anthropic(api_key=api_key)
        self._cfg = cfg

    def score(self, listing: Listing, prefs: Preferences) -> ReasonerVerdict:
        user_msg = _build_user_message(listing, prefs)
        resp = self._client.messages.create(
            model=self._cfg.model,
            max_tokens=512,
            system=SYSTEM_PROMPT,
            tools=[SCORING_TOOL],
            tool_choice={"type": "tool", "name": SCORING_TOOL["name"]},
            messages=[{"role": "user", "content": user_msg}],
        )

        tool_block = next(
            (b for b in resp.content if getattr(b, "type", None) == "tool_use"),
            None,
        )
        if tool_block is None:
            raise RuntimeError(
                f"Reasoner did not call the scoring tool. Stop reason: {resp.stop_reason}"
            )
        data = tool_block.input  # already a dict
        return ReasonerVerdict(
            score=int(data["score"]),
            verdict=str(data["verdict"]),
            summary=str(data["summary"]),
            highlights=list(data.get("highlights") or []),
            red_flags=list(data.get("red_flags") or []),
            needs_human=bool(data["needs_human"]),
        )
