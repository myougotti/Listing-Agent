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


def _require(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise RuntimeError(f"Required env var missing: {name}")
    return val


def _default_db_path() -> Path:
    """Default DB lives in ./data. Override via LISTING_AGENT_DB_PATH to
    relocate outside OneDrive if you hit sync-induced file locks."""
    return Path(__file__).resolve().parents[2] / "data" / "seen.db"


def load_config(criteria_path: Path | None = None, *, require_anthropic: bool = True) -> AppConfig:
    rapidapi_key = _require("RAPIDAPI_KEY")
    # Dry runs never construct the Reasoner, so the Anthropic key is only a
    # hard requirement for full runs. Lets you validate provider + filters
    # before setting up Anthropic billing.
    if require_anthropic:
        anthropic_key = _require("ANTHROPIC_API_KEY")
    else:
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    discord = os.environ.get("DISCORD_WEBHOOK_URL", "").strip() or None

    db_env = os.environ.get("LISTING_AGENT_DB_PATH", "").strip()
    db_path = Path(db_env) if db_env else _default_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

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
    zip_codes = [str(z) for z in (raw.get("zip_codes") or [])]
    if not zip_codes:
        raise ValueError("criteria.yaml must list at least one zip_codes entry.")

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
    )
