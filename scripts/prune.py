"""
prune.py — Apply the retention policy to option_rows. ADR-044, M3.2.

    python scripts/prune.py                 # report only. Deletes nothing.
    python scripts/prune.py --execute       # delete, after typing a confirmation

THIS IS THE ONLY SCRIPT IN THE PROJECT THAT DELETES IRREPLACEABLE DATA.
Everything about how it is built follows from that:

  - Reporting is the default and deleting needs a flag. The dangerous mode is
    never the one you get by mistyping.
  - --execute still stops and asks you to type the row count. A y/n prompt is
    answered reflexively; a number has to be read off the report above it.
  - It refuses to delete without a backup of the database newer than its last
    write, unless you say --no-backup-check in as many words.
  - Expiries used by a trade are never deleted, at any age, and the report says
    how many were held back — silence about a safety rule is how you stop
    believing it is running.

WHAT IT NEVER TOUCHES: atm_iv_by_expiry, snapshots, collection_gaps, trades.
Those are the historical record proper; only per-strike detail is prunable.

SPACE IS NOT RECLAIMED HERE. SQLite keeps the freed pages for reuse and the
file does not shrink until VACUUM, which needs exclusive access and free disk
equal to the database size. The report prints the VACUUM command to run
separately, with the collector stopped.
"""

import argparse
import contextlib
import sys
from pathlib import Path

# This script lives in scripts/ but imports project modules from the repo root.
# Same shim as check_db.py.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# These imports must follow the sys.path shim above.
import config
import db


def _force_utf8_stdout():
    """See check_db.py — same BUG-018 reasoning, same box characters."""
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _find_backup(db_path: Path) -> Path | None:
    """The newest backup that is at least as new as the database's last write.

    Looks for siblings named like the database with anything appended —
    dashboard.db.bak, dashboard.db.2026-08-09, dashboard-backup.db. Deliberately
    loose: the check exists to catch "I never made one", and an over-strict
    pattern that misses a real backup teaches you to pass --no-backup-check,
    which costs more than the check is worth.
    """
    if not db_path.exists():
        return None
    stamp = db_path.stat().st_mtime
    candidates = [
        p for p in db_path.parent.glob(f"{db_path.stem}*")
        if p != db_path and p.is_file() and not p.name.endswith(("-wal", "-shm"))
        and p.stat().st_size > 0 and p.stat().st_mtime >= stamp
    ]
    return max(candidates, key=lambda p: p.stat().st_mtime, default=None)


def _print_report(plan: dict, db_path: Path) -> None:
    print("═" * 72)
    print(f"  RETENTION PLAN — {plan['retention_days']} days past expiry"
          f"  (ADR-044)")
    print("═" * 72)
    print(f"  database        : {db_path}")
    print(f"  as of           : {plan['as_of']}")
    print(f"  cutoff          : expiries before {plan['cutoff']}")
    print(f"  option_rows now : {plan['option_rows_total']:,}")
    print()

    if plan["held_for_trades"]:
        print(f"  HELD BACK — used by a trade, never prunable "
              f"({len(plan['held_for_trades'])} expiries, "
              f"{plan['rows_held']:,} rows):")
        for entry in plan["held_for_trades"]:
            print(f"    {entry['expiry_date']}   {entry['rows']:>10,} rows")
        print()

    if not plan["prunable"]:
        print("  NOTHING TO PRUNE.")
        print()
        print("  Not an error. Collection began 2026-06-23, so nothing is old")
        print("  enough yet — see ADR-044. Re-run this after the oldest expiry")
        print(f"  passes {plan['retention_days']} days.")
        print("═" * 72)
        return

    print(f"  WOULD DELETE — {len(plan['prunable'])} expiries, "
          f"{plan['rows_to_delete']:,} rows "
          f"({100 * plan['rows_to_delete'] / max(plan['option_rows_total'], 1):.1f}%"
          f" of option_rows):")
    for entry in plan["prunable"]:
        print(f"    {entry['expiry_date']}   {entry['rows']:>10,} rows")
    print()
    print("  Kept regardless: atm_iv_by_expiry (the historical charts),")
    print("  snapshots, collection_gaps, trades.")
    print("═" * 72)


def _confirm(plan: dict) -> bool:
    """Ask for the row count in figures. Reflex-proof by construction."""
    expected = str(plan["rows_to_delete"])
    print()
    print("  This deletes irreplaceable data and cannot be undone.")
    print(f"  Type the number of rows to delete ({expected}) to proceed,")
    print("  or anything else to cancel.")
    try:
        answer = input("  > ").strip().replace(",", "")
    except (EOFError, KeyboardInterrupt):
        print("\n  Cancelled.")
        return False
    if answer != expected:
        print("  Cancelled — no rows deleted.")
        return False
    return True


def main(argv=None) -> int:
    _force_utf8_stdout()
    parser = argparse.ArgumentParser(
        description="Apply the option_rows retention policy (ADR-044).")
    parser.add_argument("--execute", action="store_true",
                        help="actually delete. Without this, reports only.")
    parser.add_argument("--days", type=int, default=None,
                        help=f"override retention days "
                             f"(default {config.RETENTION_DAYS} from config.py)")
    parser.add_argument("--today", default=None,
                        help="treat this YYYY-MM-DD as today, for rehearsal")
    parser.add_argument("--no-backup-check", action="store_true",
                        help="skip the 'is there a backup' guard. Say why to "
                             "yourself before you use it.")
    args = parser.parse_args(argv)

    db_path = Path(config.DB_PATH)
    if not db_path.exists():
        print(f"No database at {db_path}", file=sys.stderr)
        return 2

    plan = db.plan_prune(str(db_path), retention_days=args.days, today=args.today)
    _print_report(plan, db_path)

    if not args.execute:
        if plan["prunable"]:
            print()
            print("  Report only — nothing was deleted.")
            print("  Re-run with --execute to delete, after taking a backup.")
        return 0

    if not plan["prunable"]:
        return 0

    if not args.no_backup_check:
        backup = _find_backup(db_path)
        if backup is None:
            print()
            print("  REFUSING TO DELETE — no backup found newer than the database.")
            print("  Make one first:")
            print(f"      copy \"{db_path}\" \"{db_path}.bak\"")
            print("  (stop the collector first, or the copy may be mid-write)")
            print("  Then re-run. --no-backup-check overrides this.")
            return 1
        print()
        print(f"  Backup found: {backup.name}")

    if not _confirm(plan):
        return 1

    deleted = db.execute_prune(str(db_path), plan)
    print()
    print(f"  Deleted {deleted:,} option_rows.")
    if deleted != plan["rows_to_delete"]:
        print(f"  NOTE: the plan said {plan['rows_to_delete']:,}. A difference "
              f"means the collector wrote between the report and now.")
    print()
    print("  The file has NOT shrunk yet — SQLite holds the freed pages.")
    print("  To reclaim the disk space, stop the collector and run:")
    print(f"      python -c \"import sqlite3;sqlite3.connect(r'{db_path}').execute('VACUUM')\"")
    print("  VACUUM needs free disk equal to the database size.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
