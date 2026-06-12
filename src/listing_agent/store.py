"""SQLite-backed state.

Three tables:
  listings        every listing we have ever seen, by zpid
  notifications   one row per (zpid, channel), enforces no duplicate pings
  runs            audit log of poll runs for debugging

Idempotency lives in the schema (PRIMARY KEYs). Pipeline code stays simple.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
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
    raw_json   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_listings_zip ON listings(zip_code);

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

    # listings -----------------------------------------------------------

    def get(self, zpid: str) -> sqlite3.Row | None:
        with self._connect() as c:
            cur = c.execute("SELECT * FROM listings WHERE zpid = ?", (zpid,))
            return cur.fetchone()

    def upsert(self, listing: Listing) -> bool:
        """Insert or update. Returns True if this is the first time we have
        seen this zpid (a 'new' listing)."""
        now = _utcnow()
        raw = json.dumps(listing.raw, separators=(",", ":"), default=str)
        with self._connect() as c:
            cur = c.execute("SELECT zpid FROM listings WHERE zpid = ?", (listing.zpid,))
            exists = cur.fetchone() is not None
            if exists:
                c.execute(
                    """UPDATE listings
                       SET last_seen = ?, status = ?, price = ?, raw_json = ?
                       WHERE zpid = ?""",
                    (now, listing.status, listing.price, raw, listing.zpid),
                )
            else:
                c.execute(
                    """INSERT INTO listings
                       (zpid, zip_code, first_seen, last_seen, status, price, raw_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (listing.zpid, listing.zip_code, now, now, listing.status, listing.price, raw),
                )
        return not exists

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
