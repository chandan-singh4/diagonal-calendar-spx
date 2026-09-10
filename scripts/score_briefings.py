"""
score_briefings.py — grade the model's closing predictions against the tape.

    python -m scripts.score_briefings
    python -m scripts.score_briefings --since 2026-09-01

A forecast nobody grades is a forecast that is always right. This reads the
append-only log `scripts/briefing.py` writes, finds the ACTUAL close for each
session out of the snapshot record, and reports how far off each prediction
was — per briefing, and then summarised by the time of day it was made.

WHY BY TIME OF DAY. The interesting question is not "is the model any good",
it is "is it any good YET". A 09:45 reading and a 15:45 reading are different
problems: one is a forecast six hours out, the other is a forecast fifteen
minutes out with the day's structure already visible. Averaged together they
would hide exactly the thing worth knowing, which is the hour at which the
figures start to carry the close.

TWO SCORES, DELIBERATELY. The absolute error on CLOSE says how close the
single guess was; the hit rate on RANGE says whether the model knows what it
does not know. A model with a large average error and an honest 80% range hit
rate is more useful than one with a small error and a range it never lands in
— the second is confident by luck.

THE CLOSE COMES FROM THE RECORD, not from a vendor. It is the underlying price
on the last completed snapshot of the session, which is the 16:01 settle this
project already collects (ADR-049). Stated because it will not match a
printed cash close to the tick, and a scoring report that pretends otherwise
would be measuring its own drift.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

LOG = Path(config.PROJECT_ROOT) / "data" / "briefing_forecasts.jsonl"


def actual_closes(db_path: str) -> dict[str, float]:
    """The last recorded underlying price of each session, by market date.

    `date(snapshot_timestamp, '-4 hours')` is the project's own convention for
    naming the session a UTC stamp belongs to — see db.py. Reused rather than
    re-derived so this report cannot start disagreeing with the dashboard
    about which day a 20:01 snapshot was.
    """
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT date(snapshot_timestamp, '-4 hours') AS session_date, "
            "       underlying_price "
            "FROM snapshots "
            "WHERE status = 'COMPLETE' AND underlying_price IS NOT NULL "
            "ORDER BY snapshot_timestamp"
        ).fetchall()
    # Later rows overwrite earlier ones, so each session ends holding its last
    # price. A dict comprehension over an ordered query, rather than a GROUP BY
    # with a correlated MAX, which SQLite would answer more slowly and no more
    # correctly.
    return {r["session_date"]: float(r["underlying_price"]) for r in rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", default=None,
                        help="only score sessions on or after this date")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if not LOG.exists():
        print(f"No forecast log yet at {LOG}.")
        print("Run scripts/briefing.py against a session first.")
        return 1

    closes = actual_closes(config.DB_PATH)
    entries = []
    for line in LOG.read_text(encoding="utf-8").splitlines():
        if line.strip():
            entries.append(json.loads(line))

    by_time: dict[str, list[tuple[float, bool | None]]] = defaultdict(list)
    print(f"{'session':<12}{'at':>7}{'predicted':>11}{'actual':>10}"
          f"{'error':>9}  range")
    print("-" * 68)

    for entry in sorted(entries, key=lambda e: (e["session_date"], e["as_of"])):
        session = entry["session_date"]
        if args.since and session < args.since:
            continue
        actual = closes.get(session)
        predicted = entry.get("close")
        if actual is None:
            # The session has not finished, or was never collected. Skipped
            # rather than scored against a partial day, which would count a
            # forecast as wrong for a reason that is not the model's.
            print(f"{session:<12}{entry['as_of']:>7}"
                  f"{predicted or '—':>11}{'pending':>10}{'—':>9}")
            continue
        if predicted is None:
            print(f"{session:<12}{entry['as_of']:>7}{'—':>11}"
                  f"{actual:>10,.2f}{'no forecast':>9}")
            continue

        error = abs(predicted - actual)
        low, high = entry.get("low"), entry.get("high")
        hit: bool | None = None
        if low is not None and high is not None:
            hit = low <= actual <= high
        mark = "—" if hit is None else ("hit" if hit else "miss")
        span = ("—" if low is None or high is None
                else f"{low:,.0f}-{high:,.0f} {mark}")
        print(f"{session:<12}{entry['as_of']:>7}{predicted:>11,.2f}"
              f"{actual:>10,.2f}{error:>9,.1f}  {span}")
        by_time[entry["as_of"]].append((error, hit))

    if not by_time:
        print("\nNothing scoreable yet.")
        return 0

    print("\nBY TIME OF DAY")
    print(f"{'at':<8}{'n':>4}{'mean error':>13}{'worst':>9}"
          f"{'range hit rate':>17}")
    print("-" * 51)
    for clock in sorted(by_time):
        scored = by_time[clock]
        errors = [e for e, _ in scored]
        hits = [h for _, h in scored if h is not None]
        rate = (f"{sum(hits) / len(hits):.0%} ({sum(hits)}/{len(hits)})"
                if hits else "—")
        print(f"{clock:<8}{len(scored):>4}{sum(errors) / len(errors):>13,.1f}"
              f"{max(errors):>9,.1f}{rate:>17}")

    print("\nA month is the smallest honest sample. Below that, a good run and "
          "a good model look identical.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
