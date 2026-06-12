"""CLI entry point.

python -m listing_agent              # full run
python -m listing_agent --dry-run    # no Claude calls, no notifications
python -m listing_agent -v           # verbose logging
"""

from __future__ import annotations

import argparse
import sys

from .pipeline import build_and_run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="listing-agent")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and filter only. Skip reasoner and notifier.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging.")
    args = parser.parse_args(argv)

    try:
        build_and_run(dry_run=args.dry_run, verbose=args.verbose)
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        print(f"fatal: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
