"""SQLite-backed state.

Four tables:
  listings        every listing we have ever seen, by zpid
  price_history   one row per observed price change, feeds the drop detector
  notifications   one row per (zpid, channel), enforces no duplicate pings
  runs            audit log of poll runs for debugging

Idempotency lives in the schema (PRIMARY KEYs). Pipeline code stays simple.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .models import Listing

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    zpid       TEXT PRIMARY KEY,
    zip_code   TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen  TEXT NOT NULL,
    status     TEXT NOT NULL,
    price      INTEGER NOT NULL,
    url        TEXT NOT NULL DEFAULT '',
    address    TEXT NOT NULL DEFAULT '',
    raw_json   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_listings_zip ON listings(zip_code);

-- Autoincrement id rather than (zpid, observed_at): timestamps have second
-- resolution, so two observations in the same second must not collide.
CREATE TABLE IF NOT EXISTS price_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    zpid        TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    price       INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_price_history_zpid ON price_history(zpid);

CREATE TABLE IF NOT EXISTS notifications (
    zpid      TEXT NOT NULL,
    channel   TEXT NOT NULL,
    sent_at   TEXT NOT NULL,
    score     INTEGER,
    verdict   TEXT,
    PRIMARY KEY (zpid, channel)
);

CREATE TABLE IF NOT EXISTS runs (
    run_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    ended_at   TEXT,
    fetched    INTEGER DEFAULT 0,
    new_count  INTEGER DEFAULT 0,
    notified   INTEGER DEFAULT 0,
    error      TEXT
);
"""


def _utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class UpsertResult:
    """What upsert() learned about the listing. old_price is None for new
    listings; for known listings it is the price we had stored before this
    upsert, which is what the price-drop detector compares against."""

    is_new: bool
    old_price: int | None = None


class Store:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as c:
            c.executescript(SCHEMA)
            # CREATE TABLE IF NOT EXISTS never alters an existing table, so
            # columns added after a DB was first created need explicit
            # migration. Guarded by pragma so it runs at most once.
            cols = {row["name"] for row in c.execute("PRAGMA table_info(listings)")}
            for column in ("url", "address"):
                if column not in cols:
                    c.execute(f"ALTER TABLE listings ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")

    # listings -----------------------------------------------------------

    def get(self, zpid: str) -> sqlite3.Row | None:
        with self._connect() as c:
            cur = c.execute("SELECT * FROM listings WHERE zpid = ?", (zpid,))
            return cur.fetchone()

    def upsert(self, listing: Listing) -> UpsertResult:
        """Insert or update. Records a price_history row on first sight and on
        every price change, so history rows mark transitions, not polls."""
        now = _utcnow()
        raw = json.dumps(listing.raw, separators=(",", ":"), default=str)
        with self._connect() as c:
            cur = c.execute("SELECT price FROM listings WHERE zpid = ?", (listing.zpid,))
            row = cur.fetchone()
            if row is not None:
                old_price = int(row["price"])
                c.execute(
                    """UPDATE listings
                       SET last_seen = ?, status = ?, price = ?, url = ?, address = ?,
                           raw_json = ?
                       WHERE zpid = ?""",
                    (
                        now,
                        listing.status,
                        listing.price,
                        listing.url,
                        listing.address,
                        raw,
                        listing.zpid,
                    ),
                )
                if listing.price != old_price:
                    c.execute(
                        "INSERT INTO price_history (zpid, observed_at, price) VALUES (?, ?, ?)",
                        (listing.zpid, now, listing.price),
                    )
                return UpsertResult(is_new=False, old_price=old_price)

            c.execute(
                """INSERT INTO listings
                   (zpid, zip_code, first_seen, last_seen, status, price, url, address, raw_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    listing.zpid,
                    listing.zip_code,
                    now,
                    now,
                    listing.status,
                    listing.price,
                    listing.url,
                    listing.address,
                    raw,
                ),
            )
            c.execute(
                "INSERT INTO price_history (zpid, observed_at, price) VALUES (?, ?, ?)",
                (listing.zpid, now, listing.price),
            )
        return UpsertResult(is_new=True)

    def price_history(self, zpid: str) -> list[sqlite3.Row]:
        with self._connect() as c:
            cur = c.execute(
                "SELECT observed_at, price FROM price_history WHERE zpid = ? ORDER BY id",
                (zpid,),
            )
            return cur.fetchall()

    # notifications ------------------------------------------------------

    def already_notified(self, zpid: str, channel: str) -> bool:
        with self._connect() as c:
            cur = c.execute(
                "SELECT 1 FROM notifications WHERE zpid = ? AND channel = ?",
                (zpid, channel),
            )
            return cur.fetchone() is not None

    def mark_notified(
        self, zpid: str, channel: str, score: int | None, verdict: str | None
    ) -> None:
        with self._connect() as c:
            c.execute(
                """INSERT OR IGNORE INTO notifications
                   (zpid, channel, sent_at, score, verdict)
                   VALUES (?, ?, ?, ?, ?)""",
                (zpid, channel, _utcnow(), score, verdict),
            )

    # read-only queries (dashboard) ---------------------------------------

    def recent_listings(self, limit: int = 50) -> list[sqlite3.Row]:
        with self._connect() as c:
            cur = c.execute(
                """SELECT l.*,
                          (SELECT COUNT(*) - 1 FROM price_history p
                           WHERE p.zpid = l.zpid) AS price_changes
                   FROM listings l ORDER BY l.last_seen DESC LIMIT ?""",
                (limit,),
            )
            return cur.fetchall()

    def recent_notifications(self, limit: int = 50) -> list[sqlite3.Row]:
        with self._connect() as c:
            cur = c.execute(
                """SELECT n.*, l.address, l.price
                   FROM notifications n LEFT JOIN listings l ON l.zpid = n.zpid
                   ORDER BY n.sent_at DESC LIMIT ?""",
                (limit,),
            )
            return cur.fetchall()

    def recent_runs(self, limit: int = 20) -> list[sqlite3.Row]:
        with self._connect() as c:
            cur = c.execute("SELECT * FROM runs ORDER BY run_id DESC LIMIT ?", (limit,))
            return cur.fetchall()

    def counts(self) -> dict[str, int]:
        with self._connect() as c:
            return {
                "listings": c.execute("SELECT COUNT(*) FROM listings").fetchone()[0],
                "notifications": c.execute("SELECT COUNT(*) FROM notifications").fetchone()[0],
                "runs": c.execute("SELECT COUNT(*) FROM runs").fetchone()[0],
                # Each listing's first history row is its initial price, not a
                # change, so subtract one row per zpid.
                "price_changes": c.execute(
                    """SELECT COUNT(*) FROM price_history p
                       WHERE EXISTS (SELECT 1 FROM price_history q
                                     WHERE q.zpid = p.zpid AND q.id < p.id)"""
                ).fetchone()[0],
            }

    # runs ---------------------------------------------------------------

    @contextmanager
    def run(self) -> Iterator[dict]:
        """Context manager that records a run row. Yields a mutable dict you
        update during the run; finalize on exit."""
        with self._connect() as c:
            cur = c.execute("INSERT INTO runs (started_at) VALUES (?)", (_utcnow(),))
            run_id = cur.lastrowid
        stats: dict = {"fetched": 0, "new_count": 0, "notified": 0, "error": None}
        try:
            yield stats
        except Exception as e:
            stats["error"] = f"{type(e).__name__}: {e}"
            raise
        finally:
            with self._connect() as c:
                c.execute(
                    """UPDATE runs
                       SET ended_at = ?, fetched = ?, new_count = ?,
                           notified = ?, error = ?
                       WHERE run_id = ?""",
                    (
                        _utcnow(),
                        stats["fetched"],
                        stats["new_count"],
                        stats["notified"],
                        stats["error"],
                        run_id,
                    ),
                )
