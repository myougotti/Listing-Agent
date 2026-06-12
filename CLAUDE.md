# Working with Asmyou on this project

This file is auto-loaded by Claude Code at session start. It carries
context across sessions so you do not have to restate preferences.

## About the person you are working with

- Computer science student, concentration in software engineering.
- Strong C++ background from coursework. Actively sharpening C++.
- Currently learning Python. Treat Python idioms as teachable moments:
  when you use a list comprehension, context manager, dataclass,
  `Protocol`, decorator, `with` block, generator, `pathlib`, type hint
  syntax, or anything that is not obvious from C++ knowledge, say one
  sentence about what it is and why it is the Pythonic choice. Do not
  over-explain basic syntax (if/for/while/functions).
- Average math skills. Discrete math and calculus need refreshing. Avoid
  assuming fluency with notation. When math comes up, plain language first.
- Building a portfolio. Code quality, clean design, and the why matters
  as much as the what.

## How to work together

**Show me but let me think for myself.** This is the core rule. Do not
just hand over finished answers. The pattern is:

1. State the goal of the next step in one sentence.
2. Lay out the design choices and tradeoffs.
3. Ask which path before writing code, unless the choice is trivial.
4. Write the code with comments that explain the why, not the what.
5. After running, briefly explain what just happened and what to look
   for next.

**Ask before running.** Get explicit approval before:

- `pip install` of anything new
- Any command that hits the network (curl, the RapidAPI call, the
  Anthropic API)
- `pytest -v` after non-trivial changes (a brief "running tests now"
  is fine for tiny edits)
- `git commit` or `git push`
- File deletions or `rm -rf`

Reading files, `dir`, `git status`, `git diff`, and dry-runs of the
agent (`python -m listing_agent --dry-run`) do not need approval.

**Concise by default.** Long explanations only when asked or when a
genuinely subtle design decision needs justification. Bullet lists for
options, prose for thinking through tradeoffs.

**No m-dashes anywhere.** Use commas, periods, or parentheses instead.

## About this project

`listing-agent` is a periodic real estate listing watcher. It polls
RapidAPI's `zillow-com1` endpoint for new listings in configured zip
codes, scores each one with the Claude API against buyer criteria, and
notifies via Discord or console.

### Architecture (read these in order if you need context)

```
provider --> filters --> store (dedup) --> reasoner (Claude) --> notifier
```

- `src/listing_agent/config.py` loads `.env` and `criteria.yaml`
- `src/listing_agent/models.py` defines `Listing` and `ReasonerVerdict`
- `src/listing_agent/providers/zillow_rapidapi.py` is the only provider
  for now. Behind a `Provider` Protocol so swapping is one file.
- `src/listing_agent/store.py` SQLite, idempotency lives in the schema
- `src/listing_agent/filters.py` pure functions, no I/O
- `src/listing_agent/reasoner.py` Claude call with tool-use forcing
- `src/listing_agent/notifier.py` Discord and console sinks
- `src/listing_agent/pipeline.py` the only file that knows about all
  the others

All 10 tests pass in the initial scaffold. If you change something and
tests start failing, that is a signal, not noise.

## Build order

Asmyou is working through this in stages. Track which one we are on.

1. **Provider sanity check.** Real call to RapidAPI for one zip. Verify
   the response parses. Adjust `HOME_TYPE_MAP` in
   `providers/zillow_rapidapi.py` if the publisher's tokens differ.
2. **End-to-end dry run.** `python -m listing_agent --dry-run -v` with
   real `RAPIDAPI_KEY`, no Claude calls. Confirm filtering behaves.
3. **Criteria tuning.** Edit `criteria.yaml` based on what came back.
4. **Reasoner live run.** Set `LISTING_AGENT_MAX_REASONER_CALLS=2`,
   real Claude call on 1 or 2 listings, inspect the verdict.
5. **Discord wiring.** Webhook URL in `.env`. Live notify.
6. **Scheduler.** GitHub Actions workflow or Windows Task Scheduler.
   Discuss the tradeoff before picking.
7. **Stretch goals (only after 1 through 6 work).** Redfin provider,
   price-drop detector, web dashboard.

When in doubt about which stage we are on, ask.

## Environment specifics

- Windows 11, PowerShell, VS Code or Antigravity as editor.
- Project lives under OneDrive: `C:\Users\Asmyou\OneDrive\Bureau\Engineering\software\code\personal\listing-agent`.
- OneDrive plus SQLite WAL files can produce `database is locked` errors.
  If that happens, set `LISTING_AGENT_DB_PATH` in `.env` to a path
  under `%LOCALAPPDATA%`, outside OneDrive.
- Python 3.11+ in a `.venv` at the project root. Activate with
  `.\.venv\Scripts\activate`.
- `where node` may fail on this machine. Use `(Get-Command node).Path`
  if you need to resolve Node.

## What not to do

- Do not paraphrase the C++ analogy when explaining Python features.
  Compare directly: "Python's `with` block is RAII for resource
  cleanup, like a destructor that fires at scope exit."
- Do not invent fields on `Listing`. Read from `listing.raw` if the
  field is not in the dataclass.
- Do not commit `.env`, `seen.db`, or anything under `data/`.
- Do not run `pip install` of new dependencies without proposing it
  first. The dependency list in `pyproject.toml` is deliberate.
- Do not skip the tests. If a change breaks them, fix the change or
  fix the test with reason given.

## Useful commands

```powershell
# Dev workflow
python -m venv .venv
.\.venv\Scripts\activate
pip install -e ".[dev]"

# Run agent
python -m listing_agent --dry-run -v
python -m listing_agent -v

# Tests
pytest -v

# Lint
ruff check .
ruff format .
```
