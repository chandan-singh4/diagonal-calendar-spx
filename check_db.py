"""
check_db.py — Quick database health check for the SPX Diagonal Collector.

Run this anytime from a second terminal to see what the collector has gathered:
    python check_db.py

Shows:
  - Total snapshots collected today and all-time
  - Last 5 snapshots with key fields
  - IV term structure from the most recent snapshot
  - Any collection gaps recorded
"""

import sqlite3
import config


def separator(char="─", width=64):
    print(char * width)


def main():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row

    # ── Snapshot summary ──────────────────────────────────────────────────────
    separator("═")
    print("  SPX DIAGONAL COLLECTOR — DATABASE HEALTH CHECK")
    separator("═")

    total = conn.execute(
        "SELECT COUNT(*) AS n FROM snapshots WHERE status = 'COMPLETE'"
    ).fetchone()["n"]

    today = conn.execute(
        "SELECT COUNT(*) AS n FROM snapshots "
        "WHERE status = 'COMPLETE' AND DATE(snapshot_timestamp) = DATE('now')"
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

    print(f"\n  Snapshots today    : {today}")
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
