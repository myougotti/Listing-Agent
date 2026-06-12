"""One polling pass.

The only module that knows about every other module. Keep it small and
readable. If logic gets gnarly, push it into a leaf module instead.
"""

from __future__ import annotations

import logging

from rich.console import Console
from rich.logging import RichHandler

from .config import AppConfig, load_config
from .filters import check
from .notifier import ConsoleNotifier, Notifier, make_notifier
from .providers import ZillowRapidAPIProvider
from .providers.base import Provider
from .reasoner import Reasoner
from .store import Store

console = Console()


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, console=console, show_path=False)],
    )


def run_once(
    cfg: AppConfig,
    provider: Provider,
    store: Store,
    reasoner: Reasoner | None,
    notifier: Notifier,
    dry_run: bool = False,
) -> None:
    reasoner_calls = 0
    scoring_enabled = not dry_run and reasoner is not None

    with store.run() as stats:
        for zip_code in cfg.zip_codes:
            console.log(f"polling zip {zip_code} via {provider.name}")
            for listing in provider.search(zip_code, cfg.filters):
                stats["fetched"] += 1

                # Cap check must come before the upsert: an upserted listing
                # is no longer "new", so stopping after the upsert would lose
                # it instead of deferring it to the next run.
                if scoring_enabled and reasoner_calls >= cfg.max_reasoner_calls:
                    console.log("  reasoner cap hit, deferring remaining to next run")
                    return

                is_new = store.upsert(listing)
                if not is_new:
                    continue
                stats["new_count"] += 1

                # Hard filters first. Cheap.
                fr = check(listing, cfg.filters)
                if not fr.passed:
                    console.log(f"  skip {listing.zpid}: " + "; ".join(fr.reasons))
                    continue

                if dry_run or reasoner is None:
                    console.log(f"  [dry] would score {listing.zpid} {listing.address}")
                    continue

                # Don't double-notify if a prior run already did this.
                if store.already_notified(listing.zpid, notifier.channel):
                    continue

                try:
                    verdict = reasoner.score(listing, cfg.preferences)
                    reasoner_calls += 1
                except Exception as e:
                    logging.exception("reasoner failed on %s: %s", listing.zpid, e)
                    continue

                console.log(f"  scored {listing.zpid}: {verdict.verdict} ({verdict.score})")

                if verdict.score < cfg.reasoner.min_score_to_notify:
                    continue

                ok = notifier.send(listing, verdict)
                if ok:
                    store.mark_notified(
                        listing.zpid, notifier.channel, verdict.score, verdict.verdict
                    )
                    stats["notified"] += 1

        console.log(
            f"run done: fetched={stats['fetched']} new={stats['new_count']} "
            f"notified={stats['notified']}"
        )


def build_and_run(dry_run: bool = False, verbose: bool = False) -> None:
    setup_logging(verbose=verbose)
    cfg = load_config(require_anthropic=not dry_run)

    provider = ZillowRapidAPIProvider(api_key=cfg.rapidapi_key)
    store = Store(cfg.db_path)
    reasoner = None if dry_run else Reasoner(cfg.anthropic_api_key, cfg.reasoner)
    notifier: Notifier = ConsoleNotifier() if dry_run else make_notifier(cfg.discord_webhook_url)

    run_once(cfg, provider, store, reasoner, notifier, dry_run=dry_run)
