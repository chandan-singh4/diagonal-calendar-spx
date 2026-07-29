"""
check_db.py — Quick database health check for the SPX Diagonal Collector.

Run this anytime from a second terminal to see what the collector has gathered:
    python scripts/check_db.py

Shows:
  - Total snapshots collected today and all-time
  - Last 5 snapshots with key fields
  - IV term structure from the most recent snapshot
  - Any collection gaps recorded
"""

import contextlib
import datetime as dt
import sqlite3
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

# This script lives in scripts/ but imports project modules from the repo root.
# Prepend the root so `import config` resolves no matter where it is invoked
# from. Removed in M2, when the project becomes an installed package and these
# become plain `from spx_analyzer import ...` imports.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# This import must follow the sys.path shim above.
import config


def _force_utf8_stdout():
    """Make stdout able to carry the box characters this report is drawn with.

    BUG-018. The report uses ═ ─ →. When stdout is a console Python encodes
    through the Windows console API and those are fine, but when stdout is
    REDIRECTED OR PIPED Python falls back to the locale encoding — cp1252 here
    — which has no code point for any of them. The script then died with
    UnicodeEncodeError on the very first separator, before printing a line.
    Git Bash and `python scripts/check_db.py > out.txt` both take that path.

    The characters are kept and the stream widened, rather than the reverse:
    the aligned rules are what make the report scannable, and this is the first
    thing run at the start of a session.

    errors="replace" is a second belt. If some future stream cannot manage
    UTF-8 either, an unrepresentable character becomes '?' — a blemish on a
    health check, which is a great deal better than losing the health check.
    """
    for stream in (sys.stdout, sys.stderr):
        # Not every stream is a reconfigurable text stream — a test harness or
        # log capturer may have substituted its own object. Widening the
        # encoding is a convenience; failing to do it is never worth an
        # exception here, so swallow that case rather than take down a health
        # check over it.
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _et_trading_day_bounds(now=None):
    """The UTC window covering the current US Eastern day, and that day's date.

    BUG-019. Timestamps are stored in UTC and the "snapshots today" count used
    SQLite's `DATE('now')`, which is also UTC. UTC rolls over at 8pm Eastern
    (7pm on standard time), so from that moment the figure dropped to 0 while
    the collector was running normally. This is the top line of the check run
    at the start of every session, and a 0 there reads as a dead collector.

    Returns `(start_utc, end_utc, et_date)` as strings, the window half-open:
    `start <= t < end`. Stored timestamps are `YYYY-MM-DD HH:MM:SS`, which
    sorts correctly as plain text, so the comparison is a range scan on the
    existing `idx_snapshots_timestamp` index rather than a per-row conversion.

    Both midnights are built with `datetime.combine` on consecutive Eastern
    dates. The end of the day must NOT be found by converting the start to UTC
    and adding 24 hours: on the two daylight-saving changeovers an Eastern day
    is 23 or 25 hours long, so a fixed 24-hour step cuts an hour off one day
    and counts an hour of the next.

    (`start_et + timedelta(days=1)` would in fact be correct here — arithmetic
    on a zoneinfo-aware datetime is wall-clock arithmetic, so it lands on the
    next local midnight, not 24 hours later. `combine` is used anyway because
    it says "the next day's midnight" outright instead of relying on a subtlety
    most readers, including the one who wrote this, get wrong first time.)

    The conversion is done here rather than in SQL because SQLite has no
    timezone support — only fixed `'localtime'` and `'utc'` modifiers. An
    in-SQL version would either hardcode an offset that breaks twice a year, or
    depend on the machine's own timezone, which is a different bug wearing the
    same clothes.

    Args:
        now: an aware datetime, for tests. Defaults to the current instant.
    """
    eastern = ZoneInfo(config.DISPLAY_TIMEZONE)
    now_et = (now or dt.datetime.now(dt.UTC)).astimezone(eastern)

    start_et = dt.datetime.combine(now_et.date(), dt.time.min, tzinfo=eastern)
    end_et = dt.datetime.combine(
        now_et.date() + dt.timedelta(days=1), dt.time.min, tzinfo=eastern
    )

    fmt = "%Y-%m-%d %H:%M:%S"
    return (
        start_et.astimezone(dt.UTC).strftime(fmt),
        end_et.astimezone(dt.UTC).strftime(fmt),
        now_et.date().isoformat(),
    )


def separator(char="─", width=64):
    print(char * width)


def main():
    _force_utf8_stdout()

    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row

    # ── Snapshot summary ──────────────────────────────────────────────────────
    separator("═")
    print("  SPX DIAGONAL COLLECTOR — DATABASE HEALTH CHECK")
    separator("═")

    total = conn.execute(
        "SELECT COUNT(*) AS n FROM snapshots WHERE status = 'COMPLETE'"
    ).fetchone()["n"]

    # The Eastern trading day, not the UTC calendar day — see BUG-019 in
    # _et_trading_day_bounds().
    day_start_utc, day_end_utc, et_date = _et_trading_day_bounds()
    today = conn.execute(
        "SELECT COUNT(*) AS n FROM snapshots "
        "WHERE status = 'COMPLETE' "
        "AND snapshot_timestamp >= ? AND snapshot_timestamp < ?",
        (day_start_utc, day_end_utc),
    ).fetchone()["n"]

    partial = conn.execute(
        "SELECT COUNT(*) AS n FROM snapshots WHERE status = 'PARTIAL'"
    ).fetchone()["n"]

    failed = conn.execute(
        "SELECT COUNT(*) AS n FROM snapshots WHERE status = 'FAILED'"
    ).fetchone()["n"]

    option_rows = conn.execute(
        "SELECT COUNT(*) AS n FROM option_rows"
    ).fetchone()["n"]

    # The date is stated so the boundary in use is never left to guess — the
    # ambiguity is what made BUG-019 read as a collector failure.
    print(f"\n  Snapshots today    : {today}  ({et_date} ET)")
    print(f"  Snapshots all-time : {total}  (partial: {partial}  failed: {failed})")
    print(f"  Option rows stored : {option_rows:,}")

    # ── Last 5 snapshots ─────────────────────────────────────────────────────
    separator()
    print("  LAST 5 SNAPSHOTS")
    separator()
    print(f"  {'ID':>6}  {'Timestamp (UTC)':<22}  {'SPX':>8}  {'VIX':>6}  "
          f"{'Rows':>6}  {'Exp':>4}  {'ms':>6}  Status")
    separator("-")

    rows = conn.execute(
        """
        SELECT snapshot_id, snapshot_timestamp, underlying_price,
               vix_value, strikes_fetched, expiries_fetched,
               collection_latency_ms, status
        FROM snapshots
        ORDER BY snapshot_timestamp DESC
        LIMIT 5
        """
    ).fetchall()

    if not rows:
        print("  No snapshots found. Is the collector running?")
    else:
        for r in rows:
            vix_str = f"{r['vix_value']:.2f}" if r['vix_value'] else "N/A"
            print(f"  {r['snapshot_id']:>6}  {r['snapshot_timestamp']:<22}  "
                  f"{r['underlying_price']:>8.2f}  {vix_str:>6}  "
                  f"{r['strikes_fetched']:>6}  {r['expiries_fetched']:>4}  "
                  f"{r['collection_latency_ms']:>6}  {r['status']}")

    # ── IV Term structure (most recent complete snapshot) ─────────────────────
    snap = conn.execute(
        """
        SELECT snapshot_id, snapshot_timestamp, underlying_price
        FROM snapshots
        WHERE status = 'COMPLETE'
        ORDER BY snapshot_timestamp DESC
        LIMIT 1
        """
    ).fetchone()

    if snap:
        separator()
        print(f"  IV TERM STRUCTURE  —  snap={snap['snapshot_id']}  "
              f"{snap['snapshot_timestamp']} UTC  "
              f"SPX={snap['underlying_price']:.2f}")
        separator()
        print(f"  {'Expiry':<12}  {'DTE':>4}  {'ATM Strike':>10}  "
              f"{'Call IV':>8}  {'Put IV':>8}  {'Avg IV':>8}  {'vs Front':>10}")
        separator("-")

        atm_rows = conn.execute(
            """
            SELECT expiry_date, dte, atm_strike,
                   atm_call_iv, atm_put_iv, atm_avg_iv, iv_spread_to_front
            FROM atm_iv_by_expiry
            WHERE snapshot_id = ?
            ORDER BY dte
            """,
            (snap["snapshot_id"],)
        ).fetchall()

        if not atm_rows:
            print("  No ATM IV records for this snapshot.")
        else:
            for r in atm_rows:
                call_iv = f"{r['atm_call_iv']*100:.2f}%" if r['atm_call_iv'] else "  N/A"
                put_iv  = f"{r['atm_put_iv']*100:.2f}%"  if r['atm_put_iv']  else "  N/A"
                avg_iv  = f"{r['atm_avg_iv']*100:.2f}%"  if r['atm_avg_iv']  else "  N/A"
                if r['iv_spread_to_front'] is not None:
                    spread = f"+{r['iv_spread_to_front']*100:.2f}%"
                else:
                    spread = "(front)"
                print(f"  {r['expiry_date']:<12}  {r['dte']:>4}  "
                      f"{r['atm_strike']:>10.0f}  "
                      f"{call_iv:>8}  {put_iv:>8}  {avg_iv:>8}  {spread:>10}")

    # ── Collection gaps ───────────────────────────────────────────────────────
    gaps = conn.execute(
        """
        SELECT gap_start, gap_end, gap_minutes, reason
        FROM collection_gaps
        ORDER BY gap_start DESC
        LIMIT 5
        """
    ).fetchall()

    if gaps:
        separator()
        print("  RECENT COLLECTION GAPS")
        separator()
        for g in gaps:
            print(f"  {g['gap_start']}  →  {g['gap_end']}"
                  f"  ({g['gap_minutes']:.0f} min)  [{g['reason']}]")

    separator("═")
    conn.close()


if __name__ == "__main__":
    main()
