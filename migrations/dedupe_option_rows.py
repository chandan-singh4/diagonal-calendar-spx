"""
dedupe_option_rows.py — One-time standalone migration (v4.1 follow-up).

app.py's init_db() has a guarded migration that removes duplicate rows from
option_rows and creates UNIQUE(snapshot_id, expiry_date, strike, right). On a
large database that migration runs as a single full-table DELETE ... WHERE id
NOT IN (SELECT MIN(id) ... GROUP BY ...), which forces SQLite to sort the
entire table with no progress output — on a 600MB+ db this can look like the
dashboard has frozen.

This script does the exact same cleanup, but chunked by snapshot_id (using
the existing idx_option_rows_snapshot_id index), so:
  - each chunk is a few thousand rows instead of millions -> fast, no big sort
  - progress prints after every snapshot
  - it commits periodically instead of one giant transaction
  - it's idempotent: safe to stop (Ctrl+C) and re-run any time

BEFORE RUNNING:
  1. Stop collector.py if it's running (Ctrl+C in its terminal) — this script
     needs a write lock and collector.py is the only other writer.

RUN:
    python dedupe_option_rows.py

AFTER:
  - Restart collector.py, then streamlit run app.py as normal. app.py's own
    init_db() will see the UNIQUE index already exists and skip its migration
    (near-instant), exactly as designed.
"""

import sys
import time
from pathlib import Path

# This migration lives in migrations/ but imports project modules from the repo
# root. Prepend the root so the imports below resolve regardless of the
# invoking directory. Removed in M2, when the project becomes an installed
# package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402  (imports must follow the sys.path shim above)
import db      # noqa: E402


COMMIT_EVERY = 200  # snapshots per commit — keeps WAL small, gives frequent progress


def main() -> None:
    conn = db._make_conn(config.DB_PATH)  # write connection (WAL, foreign_keys ON)

    # Skip entirely if app.py's migration (or a prior run of this script)
    # already finished.
    has_index = conn.execute(
        "SELECT 1 FROM sqlite_master "
        "WHERE type = 'index' AND name = 'uq_option_rows_contract'"
    ).fetchone()
    if has_index:
        print("UNIQUE index already exists — nothing to do. "
              "You're safe to run streamlit directly.")
        conn.close()
        return

    snapshot_ids = [
        r["snapshot_id"] for r in conn.execute(
            "SELECT DISTINCT snapshot_id FROM option_rows ORDER BY snapshot_id"
        ).fetchall()
    ]
    total = len(snapshot_ids)
    print(f"Found {total:,} snapshots to check for duplicate option_rows.")
    print("(Uses the existing snapshot_id index — each snapshot is a small, "
          "fast lookup instead of one full-table scan.)\n")

    total_dupes = 0
    t_start = time.time()

    for i, snap_id in enumerate(snapshot_ids, start=1):
        cur = conn.execute(
            "DELETE FROM option_rows "
            "WHERE snapshot_id = ? AND id NOT IN ("
            "  SELECT MIN(id) FROM option_rows "
            "  WHERE snapshot_id = ? "
            "  GROUP BY expiry_date, strike, right)",
            (snap_id, snap_id),
        )
        total_dupes += cur.rowcount

        if i % COMMIT_EVERY == 0 or i == total:
            conn.commit()
            elapsed = time.time() - t_start
            rate = i / elapsed if elapsed > 0 else 0
            eta = (total - i) / rate if rate > 0 else 0
            print(
                f"  processed {i:,}/{total:,} snapshots | "
                f"{total_dupes:,} duplicate row(s) removed so far | "
                f"{elapsed:,.0f}s elapsed | ~{eta:,.0f}s remaining"
            )

    print(f"\nDedupe complete: removed {total_dupes:,} duplicate row(s) "
          f"across {total:,} snapshots in {time.time() - t_start:,.0f}s.")

    print("\nCreating UNIQUE index (prevents this from ever recurring)...")
    conn.execute(
        "CREATE UNIQUE INDEX uq_option_rows_contract "
        "ON option_rows(snapshot_id, expiry_date, strike, right)"
    )
    conn.commit()
    print("Done. This migration will not run again — streamlit will start normally now.")

    conn.close()


if __name__ == "__main__":
    main()
