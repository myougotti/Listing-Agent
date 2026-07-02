"""Read-only local dashboard over the SQLite state.

Design constraints, in order:

1. Stdlib only. Flask or FastAPI would be nicer to write but the dependency
   list in pyproject.toml is deliberate; http.server is plenty for a local,
   read-only, single-page view.
2. Read-only. The dashboard opens its own connections through Store and never
   writes, so it is safe to leave running while the poller works.
3. No JavaScript. The page is rebuilt from SQLite on every request; refresh
   the browser to refresh the data.

Run it with: python -m listing_agent --dashboard [--port 8765]
"""

from __future__ import annotations

import html
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .store import Store

log = logging.getLogger(__name__)

DEFAULT_PORT = 8765

# One neutral ink scale plus a single accent for the notified state. No
# categorical series anywhere on the page, so no palette to validate.
_CSS = """
:root {
  --ink: #1a1e24; --ink-2: #5a6472; --ink-3: #8a93a0;
  --line: #e4e7eb; --surface: #ffffff; --surface-2: #f6f7f9;
  --accent: #2563eb;
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 24px; background: var(--surface-2); color: var(--ink);
  font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
}
h1 { font-size: 18px; margin: 0 0 4px; }
.sub { color: var(--ink-3); margin: 0 0 20px; }
h2 { font-size: 13px; color: var(--ink-2); text-transform: uppercase;
     letter-spacing: 0.04em; margin: 28px 0 8px; }
.tiles { display: flex; gap: 12px; flex-wrap: wrap; }
.tile { background: var(--surface); border: 1px solid var(--line);
        border-radius: 8px; padding: 12px 16px; min-width: 150px; }
.tile .label { color: var(--ink-2); font-size: 12px; }
.tile .value { font-size: 26px; font-weight: 600; }
table { border-collapse: collapse; width: 100%; background: var(--surface);
        border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
th { text-align: left; font-size: 12px; color: var(--ink-2);
     border-bottom: 1px solid var(--line); padding: 8px 12px;
     background: var(--surface); }
td { padding: 7px 12px; border-bottom: 1px solid var(--line); }
tr:last-child td { border-bottom: none; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
th.num { text-align: right; }
.muted { color: var(--ink-3); }
.notified { color: var(--accent); font-weight: 600; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
"""


def _e(value: object) -> str:
    return html.escape("" if value is None else str(value))


def _usd(value: object) -> str:
    try:
        return f"${int(value):,}"  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "n/a"


def _tile(label: str, value: int) -> str:
    return (
        f'<div class="tile"><div class="label">{_e(label)}</div>'
        f'<div class="value">{value:,}</div></div>'
    )


def _listings_rows(store: Store) -> str:
    rows = []
    for r in store.recent_listings():
        changes = int(r["price_changes"] or 0)
        if changes:
            changes_cell = f'<td class="num">{changes}</td>'
        else:
            changes_cell = '<td class="num muted">0</td>'
        rows.append(
            "<tr>"
            f'<td><a href="{_e(r["url"] or "#")}">{_e(r["address"]) or _e(r["zpid"])}</a></td>'
            f"<td>{_e(r['zip_code'])}</td>"
            f'<td class="num">{_usd(r["price"])}</td>'
            f"{changes_cell}"
            f"<td>{_e(r['status'])}</td>"
            f'<td class="muted">{_e(r["first_seen"])}</td>'
            "</tr>"
        )
    return "".join(rows) or '<tr><td colspan="6" class="muted">No listings yet.</td></tr>'


def _notifications_rows(store: Store) -> str:
    rows = []
    for r in store.recent_notifications():
        score = r["score"]
        rows.append(
            "<tr>"
            f'<td class="muted">{_e(r["sent_at"])}</td>'
            f"<td>{_e(r['address']) or _e(r['zpid'])}</td>"
            f'<td class="num">{_usd(r["price"])}</td>'
            f'<td class="num">{_e(score) if score is not None else "n/a"}</td>'
            f"<td>{_e(r['verdict']) or 'n/a'}</td>"
            f"<td>{_e(r['channel'])}</td>"
            "</tr>"
        )
    return "".join(rows) or '<tr><td colspan="6" class="muted">No notifications yet.</td></tr>'


def _runs_rows(store: Store) -> str:
    rows = []
    for r in store.recent_runs():
        error = _e(r["error"]) if r["error"] else ""
        rows.append(
            "<tr>"
            f'<td class="num">{_e(r["run_id"])}</td>'
            f'<td class="muted">{_e(r["started_at"])}</td>'
            f'<td class="muted">{_e(r["ended_at"]) or "running"}</td>'
            f'<td class="num">{_e(r["fetched"])}</td>'
            f'<td class="num">{_e(r["new_count"])}</td>'
            f'<td class="num notified">{_e(r["notified"])}</td>'
            f"<td>{error}</td>"
            "</tr>"
        )
    return "".join(rows) or '<tr><td colspan="7" class="muted">No runs yet.</td></tr>'


def render_page(store: Store) -> str:
    counts = store.counts()
    tiles = "".join(
        [
            _tile("Listings seen", counts["listings"]),
            _tile("Price changes", counts["price_changes"]),
            _tile("Notifications sent", counts["notifications"]),
            _tile("Poll runs", counts["runs"]),
        ]
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>listing-agent dashboard</title>
<style>{_CSS}</style>
</head>
<body>
<h1>listing-agent</h1>
<p class="sub">Read-only view of the local state. Refresh for fresh data.</p>
<div class="tiles">{tiles}</div>
<h2>Recent listings</h2>
<table>
<tr><th>Address</th><th>Zip</th><th class="num">Price</th>
<th class="num">Price changes</th><th>Status</th><th>First seen</th></tr>
{_listings_rows(store)}
</table>
<h2>Notifications</h2>
<table>
<tr><th>Sent</th><th>Listing</th><th class="num">Price</th>
<th class="num">Score</th><th>Verdict</th><th>Channel</th></tr>
{_notifications_rows(store)}
</table>
<h2>Runs</h2>
<table>
<tr><th class="num">#</th><th>Started</th><th>Ended</th><th class="num">Fetched</th>
<th class="num">New</th><th class="num">Notified</th><th>Error</th></tr>
{_runs_rows(store)}
</table>
</body>
</html>"""


def serve(db_path: Path, port: int = DEFAULT_PORT) -> None:
    """Serve the dashboard on localhost until interrupted. Binds loopback
    only: this page exposes your search history, so it stays off the LAN."""
    store = Store(db_path)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (http.server API name)
            if self.path not in ("/", "/index.html"):
                self.send_error(404)
                return
            body = render_page(store).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args) -> None:
            log.debug("dashboard: " + fmt, *args)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"dashboard: http://127.0.0.1:{port} (Ctrl+C to stop)")
    try:
        server.serve_forever()
    finally:
        server.server_close()
