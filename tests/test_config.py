"""Config loading tests.

These run against a temp criteria file and monkeypatched env vars so they
never depend on the real .env or criteria.yaml in the repo.
"""

from pathlib import Path

import pytest

from listing_agent.config import load_config

MINIMAL_CRITERIA = """
zip_codes:
  - "78704"
filters:
  max_price: 750000
reasoner:
  min_score_to_notify: 70
"""


@pytest.fixture
def criteria_file(tmp_path: Path) -> Path:
    p = tmp_path / "criteria.yaml"
    p.write_text(MINIMAL_CRITERIA, encoding="utf-8")
    return p


@pytest.fixture
def base_env(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("RAPIDAPI_KEY", "rapid-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test")
    monkeypatch.setenv("LISTING_AGENT_DB_PATH", str(tmp_path / "seen.db"))
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)


def test_loads_minimal_criteria(base_env, criteria_file):
    cfg = load_config(criteria_file)
    assert cfg.zip_codes == ["78704"]
    assert cfg.filters.max_price == 750_000
    assert cfg.reasoner.min_score_to_notify == 70
    assert cfg.discord_webhook_url is None


def test_missing_rapidapi_key_raises(base_env, criteria_file, monkeypatch):
    monkeypatch.setenv("RAPIDAPI_KEY", "")
    with pytest.raises(RuntimeError, match="RAPIDAPI_KEY"):
        load_config(criteria_file)


def test_missing_anthropic_key_raises_on_full_run(base_env, criteria_file, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        load_config(criteria_file)


def test_dry_run_does_not_require_anthropic_key(base_env, criteria_file, monkeypatch):
    # Stage 2 of the build order: dry runs only need the RapidAPI key.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    cfg = load_config(criteria_file, require_anthropic=False)
    assert cfg.anthropic_api_key == ""
    assert cfg.rapidapi_key == "rapid-test"


def test_missing_criteria_file_raises(base_env, tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.yaml")


def test_empty_zip_codes_raises(base_env, tmp_path):
    p = tmp_path / "criteria.yaml"
    p.write_text("zip_codes: []\n", encoding="utf-8")
    with pytest.raises(ValueError, match="zip_codes"):
        load_config(p)


def test_defaults_for_provider_and_price_drops(base_env, criteria_file):
    cfg = load_config(criteria_file)
    assert cfg.provider == "zillow_rapidapi"
    assert cfg.price_drops.enabled is True
    assert cfg.price_drops.min_drop_pct == 3.0


def test_price_drops_section_is_parsed(base_env, tmp_path):
    p = tmp_path / "criteria.yaml"
    p.write_text(
        MINIMAL_CRITERIA + "\nprice_drops:\n  enabled: false\n  min_drop_pct: 5\n",
        encoding="utf-8",
    )
    cfg = load_config(p)
    assert cfg.price_drops.enabled is False
    assert cfg.price_drops.min_drop_pct == 5


def test_redfin_provider_does_not_require_rapidapi_key(base_env, tmp_path, monkeypatch):
    # Redfin's endpoints are unauthenticated; requiring RAPIDAPI_KEY anyway
    # would block the provider swap the Protocol exists for.
    monkeypatch.setenv("RAPIDAPI_KEY", "")
    p = tmp_path / "criteria.yaml"
    p.write_text(MINIMAL_CRITERIA + '\nprovider: "redfin"\n', encoding="utf-8")
    cfg = load_config(p)
    assert cfg.provider == "redfin"
    assert cfg.rapidapi_key == ""
