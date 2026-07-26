"""
capture_scanner_golden.py — freeze today's scanner behaviour before M2 moves it.

WHY
---
M2 breaks app.py apart, and the scanner logic moves with it. The danger is not
a crash — that would be obvious. It is that the scanner comes out the other
side subtly different, and weeks of trading decisions get made against a screen
that quietly shifted, with nothing to compare against.

So: run today's scanner over real stored market data, and freeze both the INPUT
and the OUTPUT to disk. tests/test_scanner_golden.py then replays the same
input after the refactor and demands the same output.

This asserts NOTHING about whether today's answers are correct. It cannot — that
would require validating the strategy, which is an open question (M6). It
guarantees only that M2 did not change them. That is the honest guarantee to
make about code you are moving but not fixing, and baking in a guess about
correctness would be worse than useless.

SAFETY
------
The production database is opened READ-ONLY via SQLite's `mode=ro` URI, so this
script cannot write, lock, or migrate the 1.4 GB of irreplaceable history. It is
also the only place that touches it — the test suite itself reads only the
captured fixtures and never opens a database at all.

USAGE
-----
  python scripts/capture_scanner_golden.py            # capture
  python scripts/capture_scanner_golden.py --verify   # re-run, compare, no write

Re-capture ONLY when you have deliberately changed the scanner and reviewed the
resulting diff. Re-capturing to make a failing test pass destroys the very
protection this file exists to give.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from app_loader import load_scanner_functions  # noqa: E402

DB_PATH = ROOT / "data" / "dashboard.db"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "scanner"

# Two snapshots from different sessions. One would prove the code runs; two
# make an accidental hard-coding of one day's numbers far less likely to pass.
N_SNAPSHOTS = 2


def _load_chain_df(conn: sqlite3.Connection, snapshot_id: int) -> pd.DataFrame:
    """Rebuild chain_df exactly as app.py's _load_chain_df does.

    Kept deliberately in step with app.py:1353-1363, including the x100 IV load
    boundary. If that construction changes, this must change with it or the
    fixture stops representing what the dashboard actually feeds the scanner.
    """
    rows = conn.execute(
        "SELECT * FROM option_rows WHERE snapshot_id = ? "
        "ORDER BY expiry_date, strike, right",
        (snapshot_id,),
    ).fetchall()
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame([dict(r) for r in rows])
    df = df.rename(columns={"expiry_date": "expiry"})
    df["side"] = df["right"].map({"C": "CALL", "P": "PUT"})
    df["iv"] = df["iv"] * 100  # decimal -> percent, at the load boundary
    return df


def _pick_snapshots(conn: sqlite3.Connection) -> list[tuple[int, str, float]]:
    """The most recent COMPLETE snapshot from each of the last N sessions."""
    return conn.execute(
        """
        SELECT snapshot_id, snapshot_timestamp, underlying_price
        FROM snapshots
        WHERE status = 'COMPLETE'
          AND snapshot_id IN (
              SELECT MAX(snapshot_id) FROM snapshots
              WHERE status = 'COMPLETE'
              GROUP BY DATE(snapshot_timestamp)
          )
        ORDER BY snapshot_id DESC
        LIMIT ?
        """,
        (N_SNAPSHOTS,),
    ).fetchall()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verify", action="store_true",
                    help="Recompute and compare against the stored golden files; write nothing.")
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"ERROR: database not found at {DB_PATH}")
        return 1

    scanner = load_scanner_functions()
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    failures = 0
    try:
        snapshots = _pick_snapshots(conn)
        if not snapshots:
            print("ERROR: no COMPLETE snapshots in the database.")
            return 1

        for snapshot_id, ts, spx in snapshots:
            chain_df = _load_chain_df(conn, snapshot_id)
            if chain_df.empty:
                print(f"  skip snapshot {snapshot_id}: no option rows")
                continue

            out = scanner["_compute_transform_scanner"](
                chain_df, float(spx), snapshot_id,
                put_offset=0, call_offset=0, max_rows=50,
            )

            tag = f"snapshot_{snapshot_id}"
            in_path = FIXTURE_DIR / f"{tag}_input.csv.gz"
            out_path = FIXTURE_DIR / f"{tag}_scanner.csv.gz"
            meta_path = FIXTURE_DIR / f"{tag}_meta.txt"

            if args.verify:
                expected = pd.read_csv(out_path)
                actual = out.reset_index(drop=True)
                try:
                    pd.testing.assert_frame_equal(
                        expected, actual, check_dtype=False, rtol=1e-9, atol=1e-9
                    )
                    print(f"  OK    {tag}: {len(actual)} rows match")
                except AssertionError as exc:
                    failures += 1
                    print(f"  DIFF  {tag}:\n{exc}")
            else:
                chain_df.to_csv(in_path, index=False, compression="gzip")
                out.reset_index(drop=True).to_csv(out_path, index=False, compression="gzip")
                meta_path.write_text(
                    f"snapshot_id      = {snapshot_id}\n"
                    f"snapshot_time    = {ts}\n"
                    f"underlying_price = {spx}\n"
                    f"chain_rows       = {len(chain_df)}\n"
                    f"scanner_rows     = {len(out)}\n"
                    f"captured_from    = data/dashboard.db (read-only)\n",
                    encoding="utf-8",
                )
                print(f"  captured {tag}: {len(chain_df)} chain rows -> {len(out)} scanner rows")
    finally:
        conn.close()

    if args.verify:
        print("\nVERIFY:", "FAILED" if failures else "all snapshots match")
        return 1 if failures else 0

    print(f"\nWrote fixtures to {FIXTURE_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())