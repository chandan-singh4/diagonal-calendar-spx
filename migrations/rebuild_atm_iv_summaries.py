"""Rebuild atm_iv_by_expiry rows for named snapshots from their option_rows.

WHY THIS EXISTS
  On 2026-08-19 four snapshots (4801-4804) were written while the collector was
  mid-fix. Their option_rows are complete and carry settlement, but only ONE
  summary row per snapshot reached atm_iv_by_expiry instead of twenty. The
  prices are intact, so the summaries can be recomputed exactly rather than
  guessed at — nothing here invents a number that was not collected.

  It is also the tool that back-fills the settlement column (BUG-028) for any
  snapshot whose summaries predate it, should that ever be wanted.

WHAT IT DOES NOT DO
  It does not touch option_rows, and it does not decide anything about
  `status`. A snapshot marked PARTIAL because its summaries were missing is
  still marked PARTIAL afterwards unless --fix-status is passed, because that
  is a separate claim about the data and belongs to whoever runs this.

HOW IT COMPUTES
  By calling collector._compute_atm_iv_records — the same function the live
  collector uses. A second implementation here would be free to drift from the
  collector silently, and a rebuild that disagreed with the rows either side of
  it would be worse than the gap it repaired.

USAGE
  python -m migrations.rebuild_atm_iv_summaries --db PATH 4801-4804 --dry-run
  python -m migrations.rebuild_atm_iv_summaries --db PATH 4801-4804 --apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import collector  # noqa: E402
import config  # noqa: E402
import db  # noqa: E402


def parse_snapshots(specs: list[str]) -> list[int]:
    """"4801-4804" and "4801 4804" both mean what they look like."""
    out: list[int] = []
    for spec in specs:
        if "-" in spec:
            lo, hi = (int(p) for p in spec.split("-", 1))
            out.extend(range(lo, hi + 1))
        else:
            out.append(int(spec))
    return sorted(set(out))


def _frame(conn, snapshot_id: int) -> pd.DataFrame:
    """The snapshot's option_rows in the shape _compute_atm_iv_records wants.

    iv goes back to PERCENT here. option_rows stores decimals and the collector
    is handed the broker's percentages, so feeding stored decimals straight in
    would divide by a hundred a second time and record IVs of 0.0018.
    """
    rows = conn.execute(
        """
        SELECT expiry_date, settlement, dte, strike, right, iv
        FROM option_rows
        WHERE snapshot_id = ? AND iv IS NOT NULL
        """,
        (snapshot_id,),
    ).fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([dict(r) for r in rows])
    return pd.DataFrame({
        "expiry":     df["expiry_date"],
        "settlement": df["settlement"],
        "dte":        df["dte"],
        "strike":     df["strike"],
        "side":       df["right"].map({"C": "CALL", "P": "PUT"}),
        "iv":         df["iv"] * 100.0,
    })


def rebuild(db_path: str, snapshot_ids: list[int], *,
            apply: bool, fix_status: bool) -> int:
    """Returns the number of snapshots that would change / did change."""
    changed = 0
    # get_conn is read-only by design (PRAGMA query_only). A dry run could use
    # it, but then the two modes would exercise different connections and only
    # the destructive one would be untried.
    opener = db.managed_conn if apply else db.get_conn
    with opener(db_path) as conn:
        for sid in snapshot_ids:
            snap = conn.execute(
                "SELECT underlying_price, status, expiries_fetched "
                "FROM snapshots WHERE snapshot_id = ?", (sid,),
            ).fetchone()
            if snap is None:
                print(f"  {sid}: no such snapshot — skipped")
                continue
            if snap["underlying_price"] is None:
                print(f"  {sid}: no underlying price recorded — skipped, "
                      f"the a.t.m. strike cannot be chosen without it")
                continue

            before = conn.execute(
                "SELECT COUNT(*) FROM atm_iv_by_expiry WHERE snapshot_id = ?",
                (sid,),
            ).fetchone()[0]

            frame = _frame(conn, sid)
            records = collector._compute_atm_iv_records(
                frame, float(snap["underlying_price"]), sid)
            dates = len({r["expiry_date"] for r in records})

            print(f"  {sid}: {before} summary row(s) -> {len(records)} "
                  f"({dates} expiry dates), status {snap['status']}")
            if not records:
                print(f"  {sid}: nothing to write — skipped rather than "
                      f"deleting what is there")
                continue
            changed += 1

            if not apply:
                continue

            conn.execute("DELETE FROM atm_iv_by_expiry WHERE snapshot_id = ?",
                         (sid,))
            conn.executemany(
                """
                INSERT INTO atm_iv_by_expiry (
                    snapshot_id, expiry_date, settlement, dte, atm_strike,
                    atm_call_iv, atm_put_iv, atm_avg_iv,
                    iv_spread_to_front, iv_ratio_to_front
                ) VALUES (
                    :snapshot_id, :expiry_date, :settlement, :dte, :atm_strike,
                    :atm_call_iv, :atm_put_iv, :atm_avg_iv,
                    :iv_spread_to_front, :iv_ratio_to_front
                )
                """,
                records,
            )
            if fix_status:
                # The note is kept rather than cleared. These four snapshots
                # really were damaged for a few hours and the record of it is
                # worth more than a tidy NULL.
                conn.execute(
                    "UPDATE snapshots SET status = 'COMPLETE', "
                    "expiries_fetched = ?, error_message = ? "
                    "WHERE snapshot_id = ?",
                    (dates,
                     f"ATM IV summaries rebuilt from intact option_rows "
                     f"(BUG-026); {dates} expiries",
                     sid),
                )
            conn.commit()

    return changed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("snapshots", nargs="+",
                    help='snapshot ids or ranges, e.g. 4801-4804')
    ap.add_argument("--db", default=config.DB_PATH)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true",
                      help="report what would change and write nothing")
    mode.add_argument("--apply", action="store_true")
    ap.add_argument("--fix-status", action="store_true",
                    help="also mark the rebuilt snapshots COMPLETE. Only correct "
                         "when the snapshot was marked PARTIAL BECAUSE of the "
                         "missing summaries and nothing else was wrong with it.")
    args = ap.parse_args(argv)

    ids = parse_snapshots(args.snapshots)
    print(f"{'APPLYING to' if args.apply else 'DRY RUN against'} {args.db}")
    print(f"snapshots: {ids}")
    changed = rebuild(args.db, ids, apply=args.apply, fix_status=args.fix_status)
    print(f"{changed} snapshot(s) {'rebuilt' if args.apply else 'would change'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
