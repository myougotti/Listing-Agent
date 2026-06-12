# listing-agent

Polls real estate listings for a set of zip codes, scores each new one with
Claude against your buyer criteria, and pings a notification sink when a
listing clears your threshold.

## Why this exists

Zillow retired its public Web Services API in September 2021. Today the
realistic ways to get listing data without an MLS relationship are
third-party REST wrappers (RapidAPI's `zillow-com1`, APIllow, Zillapi) and
scraping. This project targets RapidAPI's `zillow-com1` as the default,
and isolates that choice behind a `Provider` Protocol so swapping providers
is one file.

## Architecture

```
provider -> filters -> store (dedup) -> reasoner (Claude) -> notifier
                                   ^
                                   |
                                   +--- runs / notifications log
```

Each leaf module is independently testable. Only `pipeline.py` knows about
all of them. Idempotency lives in the SQLite schema (composite PK on
`notifications`), not in app logic.

## Setup

```powershell
# from the project root
python -m venv .venv
.\.venv\Scripts\activate
pip install -e ".[dev]"

copy .env.example .env                  # then edit
copy criteria.example.yaml criteria.yaml # then edit
```

Required secrets in `.env`:
- `RAPIDAPI_KEY` from https://rapidapi.com after subscribing to `zillow-com1`
- `ANTHROPIC_API_KEY` from https://console.anthropic.com
  (not needed for `--dry-run`)

Optional:
- `DISCORD_WEBHOOK_URL` for notifications. Without it the agent prints to console.
- `LISTING_AGENT_DB_PATH` to relocate the SQLite DB outside OneDrive (see below).

## Run

```powershell
# Full pass (fetches, scores, notifies)
python -m listing_agent

# Dry run: fetches and filters, no Claude, no notifications
# (only RAPIDAPI_KEY required)
python -m listing_agent --dry-run

# Verbose
python -m listing_agent -v

# Tests
pytest
```

## OneDrive gotcha

If you keep the working directory under OneDrive, the sync client can grab
an exclusive lock on `seen.db` while SQLite is mid-write. The symptom is
`database is locked` errors that come and go. Two options:

1. Set `LISTING_AGENT_DB_PATH=C:\Users\Asmyou\AppData\Local\listing-agent\seen.db`
   in `.env`. The DB lives outside sync. State is local only.
2. In OneDrive settings, exclude the `data/` folder from sync.

Option 1 is cleaner.

## Scheduling

Two options. Pick one.

**GitHub Actions** (runs in the cloud, free, requires you to push secrets
to the repo). See `.github/workflows/poll.yml`. State is committed back
to the repo at the end of each run. Add secrets at
`Settings -> Secrets and variables -> Actions`.

**Windows Task Scheduler** (runs when your machine is on). A registration
helper lives at `scripts/register-task.ps1`:

```powershell
# from an elevated PowerShell, from the project root
.\scripts\register-task.ps1            # registers a 30-minute repeating task
.\scripts\register-task.ps1 -Unregister # removes it
```

Or set it up by hand:
- Trigger: every 30 minutes
- Action: `C:\Path\To\.venv\Scripts\python.exe -m listing_agent`
- Start in: project root

## Project layout

```
src/listing_agent/
  config.py        load .env and criteria.yaml
  models.py        Listing, ReasonerVerdict
  store.py         SQLite (listings, notifications, runs)
  filters.py       pure functions over Listing
  reasoner.py      Claude scorer (tool-use forced JSON)
  notifier.py      Discord + console sinks
  providers/
    base.py        Provider Protocol
    zillow_rapidapi.py
  pipeline.py      one-pass orchestration
  cli.py           argparse entry point
```

## Reasoner output schema

The reasoner is forced to call exactly one tool, `report_listing_assessment`,
whose `input_schema` is:

```
score        int 0..100
verdict      STRONG_MATCH | MATCH | SOFT_PASS | REJECT
summary      one sentence, <=30 words
highlights   list[str], max 4
red_flags    list[str], max 4
needs_human  bool
```

Tool-use forcing eliminates "the model wrapped JSON in markdown" failure
modes. If the model deviates, the call raises and the listing is skipped
rather than producing garbage state.

## Adding a new provider

1. Create `src/listing_agent/providers/<name>.py`.
2. Implement the `Provider` Protocol from `base.py`.
3. Export it in `providers/__init__.py`.
4. Wire it in `pipeline.build_and_run` (or make the provider configurable
   via `criteria.yaml`).

The pipeline never touches provider internals, so this is the only file
that changes.

## Cost ceiling

`LISTING_AGENT_MAX_REASONER_CALLS` caps Claude calls per run. Defaults to
25. The cap is a hard fence, not a soft warning: once hit, the run stops
before recording the remaining listings, so the next run still sees them
as new and scores them then.

## Legal note

Third-party wrappers like `zillow-com1` typically scrape Zillow under the
hood. Zillow's Terms of Use prohibit automated access. Personal,
non-redistributive use is a different risk profile from commercial
distribution, but you assume the risk either way. If you intend to ship
this as a product, look at Bridge Interactive (requires MLS affiliation)
or licensed providers like ATTOM.
