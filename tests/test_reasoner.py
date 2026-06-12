"""Reasoner tests with a mocked Anthropic client. No network, no spend.

The patch target is listing_agent.reasoner.Anthropic (the name as imported
into the reasoner module), not anthropic.Anthropic. Patching where a name
is *looked up* is what makes the mock take effect.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from listing_agent.config import Preferences, ReasonerConfig
from listing_agent.models import Listing
from listing_agent.reasoner import Reasoner


def make_listing() -> Listing:
    return Listing(
        zpid="42",
        zip_code="78704",
        price=525_000,
        beds=3,
        baths=2,
        sqft=1620,
        status="FOR_SALE",
        home_type="SINGLE_FAMILY",
        url="https://example.com/42",
        address="42 Test Ln",
    )


def make_response(*blocks) -> SimpleNamespace:
    return SimpleNamespace(content=list(blocks), stop_reason="tool_use")


def tool_block(**overrides) -> SimpleNamespace:
    data = {
        "score": 84,
        "verdict": "MATCH",
        "summary": "Solid fit with a small yard.",
        "highlights": ["garage", "yard"],
        "red_flags": [],
        "needs_human": False,
    }
    data.update(overrides)
    return SimpleNamespace(type="tool_use", input=data)


@pytest.fixture
def mock_client():
    with patch("listing_agent.reasoner.Anthropic") as cls:
        client = MagicMock()
        cls.return_value = client
        yield client


def test_score_parses_tool_output(mock_client):
    mock_client.messages.create.return_value = make_response(tool_block())

    r = Reasoner(api_key="test", cfg=ReasonerConfig())
    verdict = r.score(make_listing(), Preferences(must_have=["garage"]))

    assert verdict.score == 84
    assert verdict.verdict == "MATCH"
    assert verdict.highlights == ["garage", "yard"]
    assert verdict.needs_human is False


def test_score_forces_the_scoring_tool(mock_client):
    mock_client.messages.create.return_value = make_response(tool_block())

    r = Reasoner(api_key="test", cfg=ReasonerConfig(model="claude-sonnet-4-6"))
    r.score(make_listing(), Preferences())

    kwargs = mock_client.messages.create.call_args.kwargs
    assert kwargs["model"] == "claude-sonnet-4-6"
    assert kwargs["tool_choice"] == {"type": "tool", "name": "report_listing_assessment"}
    # The criteria travel in the user turn, not the system prompt.
    assert "BUYER PREFERENCES" in kwargs["messages"][0]["content"]


def test_score_raises_when_no_tool_block(mock_client):
    text_only = SimpleNamespace(type="text", text="I refuse to use tools.")
    mock_client.messages.create.return_value = make_response(text_only)

    r = Reasoner(api_key="test", cfg=ReasonerConfig())
    with pytest.raises(RuntimeError, match="did not call the scoring tool"):
        r.score(make_listing(), Preferences())


def test_fact_sheet_marks_missing_fields_as_unknown(mock_client):
    mock_client.messages.create.return_value = make_response(tool_block())

    r = Reasoner(api_key="test", cfg=ReasonerConfig())
    listing = Listing(
        zpid="7",
        zip_code="78704",
        price=400_000,
        beds=2,
        baths=1,
        sqft=None,
        status="FOR_SALE",
        home_type=None,
        url="https://example.com/7",
        address="7 Mystery Rd",
    )
    r.score(listing, Preferences())

    sent = mock_client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "sqft: unknown" in sent
    assert "home_type: unknown" in sent
