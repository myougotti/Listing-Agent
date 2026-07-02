"""Centralized config loading.

Reads .env for secrets and criteria.yaml for search rules. Everything that
varies between runs goes through here so no other module touches os.environ
or reads files directly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

# Load .env once at import time. Safe to call repeatedly.
load_dotenv()


@dataclass(frozen=True)
class HardFilters:
    max_price: int | None = None
    min_price: int | None = None
    min_beds: float | None = None
    min_baths: float | None = None
    min_sqft: int | None = None
    max_sqft: int | None = None
    home_types: list[str] = field(default_factory=list)
    exclude_statuses: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Preferences:
    must_have: list[str] = field(default_factory=list)
    nice_to_have: list[str] = field(default_factory=list)
    deal_breakers: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ReasonerConfig:
    min_score_to_notify: int = 70
    model: str = "claude-sonnet-4-6"


@dataclass(frozen=True)
class PriceDropConfig:
    """A drop notifies without a Claude call: the listing already interested
    us enough to store, and the delta itself is the news."""

    enabled: bool = True
    min_drop_pct: float = 3.0  # ignore drops smaller than this percentage


@dataclass(frozen=True)
class AppConfig:
    rapidapi_key: str
    anthropic_api_key: str
    discord_webhook_url: str | None
    db_path: Path
    max_reasoner_calls: int
    zip_codes: list[str]
    filters: HardFilters
    preferences: Preferences
    reasoner: ReasonerConfig
    # Defaulted fields keep older call sites and tests valid.
    provider: str = "zillow_rapidapi"
    price_drops: PriceDropConfig = field(default_factory=PriceDropConfig)


def _require(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise RuntimeError(f"Required env var missing: {name}")
    return val


def _default_db_path() -> Path:
    """Default DB lives in ./data. Override via LISTING_AGENT_DB_PATH to
    relocate outside OneDrive if you hit sync-induced file locks."""
    return Path(__file__).resolve().parents[2] / "data" / "seen.db"


def resolve_db_path() -> Path:
    """DB location without loading full config. The dashboard uses this so it
    can run with no API keys and no criteria.yaml present."""
    db_env = os.environ.get("LISTING_AGENT_DB_PATH", "").strip()
    db_path = Path(db_env) if db_env else _default_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return db_path


def load_config(criteria_path: Path | None = None, *, require_anthropic: bool = True) -> AppConfig:
    # Dry runs never construct the Reasoner, so the Anthropic key is only a
    # hard requirement for full runs. Lets you validate provider + filters
    # before setting up Anthropic billing.
    if require_anthropic:
        anthropic_key = _require("ANTHROPIC_API_KEY")
    else:
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    discord = os.environ.get("DISCORD_WEBHOOK_URL", "").strip() or None

    db_path = resolve_db_path()

    max_calls = int(os.environ.get("LISTING_AGENT_MAX_REASONER_CALLS", "25"))

    if criteria_path is None:
        env_path = os.environ.get("LISTING_AGENT_CRITERIA", "").strip()
        criteria_path = Path(env_path) if env_path else Path("criteria.yaml")
    if not criteria_path.exists():
        raise FileNotFoundError(
            f"Criteria file not found: {criteria_path}. "
            "Copy criteria.example.yaml to criteria.yaml and edit it."
        )

    with criteria_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    filters = HardFilters(**(raw.get("filters") or {}))
    prefs = Preferences(**(raw.get("preferences") or {}))
    reasoner = ReasonerConfig(**(raw.get("reasoner") or {}))
    price_drops = PriceDropConfig(**(raw.get("price_drops") or {}))
    provider = str(raw.get("provider") or "zillow_rapidapi")
    zip_codes = [str(z) for z in (raw.get("zip_codes") or [])]
    if not zip_codes:
        raise ValueError("criteria.yaml must list at least one zip_codes entry.")

    # Only the Zillow RapidAPI provider needs the key; Redfin's endpoints are
    # unauthenticated. Requiring it unconditionally would block provider swaps.
    if provider == "zillow_rapidapi":
        rapidapi_key = _require("RAPIDAPI_KEY")
    else:
        rapidapi_key = os.environ.get("RAPIDAPI_KEY", "").strip()

    return AppConfig(
        rapidapi_key=rapidapi_key,
        anthropic_api_key=anthropic_key,
        discord_webhook_url=discord,
        db_path=db_path,
        max_reasoner_calls=max_calls,
        zip_codes=zip_codes,
        filters=filters,
        preferences=prefs,
        reasoner=reasoner,
        provider=provider,
        price_drops=price_drops,
    )
