"""
reclassify_collection_gaps.py — One-time standalone migration (BUG-005).

Every row in `collection_gaps` was written as COLLECTOR_OFFLINE. The classifier
that produced them measured the wrong thing (see ADR-024 / BUG-005): it guessed
at whether a gap mattered using clock-time heuristics instead of asking how much
open market the gap actually covered. Ordinary nights were recorded as faults,
and — worse — genuinely lost trading days were labelled routine and discarded.

collector.py is fixed. This script brings the EXISTING rows into line, using the
same `_classify_gap()` the fixed collector now uses, so the historical record
agrees with the code that will maintain it.

WHAT IT CHANGES
  reason                   — recomputed from market minutes missed
  expected_snapshots_lost  — recomputed from market minutes, not wall clock.
                             The stored figures total 19,759 lost snapshots,
                             which is more than the database has ever held
                             (2,605). The truthful total is 145.
  notes                    — the previous values are APPENDED, not overwritten,
                             so this migration is self-documenting and its
                             effect can be read back off the row itself.

WHAT IT DOES NOT CHANGE
  Nothing outside `collection_gaps`. No snapshot, option row or ATM IV record is
  touched. `collection_gaps` is a pure audit log — nothing reads it today
  (OPS-005 tracks surfacing it in the UI), so there is no downstream chart or
  number that can shift as a result.

  It also does NOT delete the ~22 rows that record the 15:30 session-change
  artefact. Those rows describe non-events: an ordinary 5-minute MIDDAY interval
  judged against the 60-second CLOSE threshold. They arguably should not exist,
  but deleting rows from an audit log on a judgement call is not this script's
  business. They are left as COLLECTOR_OFFLINE with a note, and flagged in the
  backlog for a deliberate decision.

SAFETY
  - Idempotent: re-running changes nothing further (the note marker is checked).
  - --dry-run (default) prints the plan and writes nothing.
  - Takes a write lock only for the UPDATE, and only on 47 rows.
  - A backup is verified to exist before any write. The collector need not be
    stopped: this is a handful of row updates on a table it appends to rarely,
    and WAL allows the writer to continue.

RUN
    python migrations/reclassify_collection_gaps.py            # dry run
    python migrations/reclassify_collection_gaps.py --apply    # write
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # noqa: E402

import collector                                                  # noqa: E402
import config                                                     # noqa: E402
import db                                                         # noqa: E402

_MARKER = "[reclassified BUG-005]"
_BACKUP_DIR = Path(r"C:\Users\chand\Python\spx-dashboard-backups")


def _parse(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _verify_backup() -> None:
    """Refuse to write without a backup on disk. Cheap insurance on a 1.4 GB
    irreplaceable file, even for a 47-row metadata update."""
    backups = sorted(_BACKUP_DIR.glob("dashboard-*.db"), key=lambda p: p.stat().st_mtime)
    if not backups:
        sys.exit(f"ABORT: no backup found in {_BACKUP_DIR}. Run the backup first.")
    newest = backups[-1]
    age_h = (datetime.now().timestamp() - newest.stat().st_mtime) / 3600
    print(f"Backup found : {newest.name}  "
          f"({newest.stat().st_size / 1024**3:.2f} GB, {age_h:.0f}h old)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="write the changes (default is a dry run)")
    args = ap.parse_args()

    with db.get_conn(config.DB_PATH) as conn:          # query_only=ON
        rows = conn.execute(
            "SELECT id, gap_start, gap_end, expected_snapshots_lost, reason, notes "
            "FROM collection_gaps ORDER BY gap_start"
        ).fetchall()

    plan, changes = [], Counter()
    lost_before = lost_after = 0
    already_done = 0

    for r in rows:
        if r["notes"] and _MARKER in r["notes"]:
            already_done += 1
            continue

        start, end = _parse(r["gap_start"]), _parse(r["gap_end"])
        new_reason = collector._classify_gap(start, end)
        market_min = collector.market_minutes_between(start, end)
        new_lost = int(market_min / (config.POLL_INTERVAL_NORMAL / 60))

        old_lost = r["expected_snapshots_lost"] or 0
        lost_before += old_lost
        lost_after += new_lost
        changes[(r["reason"], new_reason)] += 1

        note = (f"{r['notes'] or ''} | {_MARKER} was reason={r['reason']} "
                f"lost={old_lost}; market_minutes={market_min:.0f}").strip(" |")
        plan.append((r["id"], new_reason, new_lost, note,
                     r["reason"], old_lost, market_min))

    if already_done:
        print(f"{already_done} row(s) already reclassified — skipping them.")
    if not plan:
        print("Nothing to do.")
        return

    print(f"\n{len(plan)} row(s) to update\n")
    for rid, new_r, new_l, _, old_r, old_l, mkt in plan:
        flag = "" if old_r == new_r else "  <-- RECLASSIFY"
        print(f"  id={rid:<4} {old_r:<18} -> {new_r:<18} "
              f"lost {old_l:>5} -> {new_l:<4} (market {mkt:>5.0f} min){flag}")

    print("\nSUMMARY")
    for (old, new), n in sorted(changes.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>3}  {old} -> {new}"
              f"{'' if old == new else '   RECLASSIFY'}")
    print(f"\n  snapshots-lost claimed : {lost_before:,}")
    print(f"  snapshots-lost in truth: {lost_after:,}")

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to commit.")
        return

    _verify_backup()
    with db.managed_conn(config.DB_PATH) as conn:
        conn.executemany(
            "UPDATE collection_gaps SET reason = ?, expected_snapshots_lost = ?, "
            "notes = ? WHERE id = ?",
            [(new_r, new_l, note, rid) for rid, new_r, new_l, note, *_ in plan],
        )
    print(f"\nApplied to {len(plan)} row(s).")


if __name__ == "__main__":
    main()