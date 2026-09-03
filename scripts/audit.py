"""audit.py — does the record contain what it should? (M3.7)

WHY THIS EXISTS, precisely. Three times in three sessions, data was never
captured and nothing could tell:

  - the p.m. third-Friday contract, thrown away every cycle for eight weeks
    (ADR-046). Found by Chandan noticing the screen.
  - the closing price, never recorded on any day for ten weeks (ADR-049).
    Found by Chandan asking why collection stopped at 3:59.
  - the a.m. contract carried a session too long (ADR-048).

**Every check in the system passed throughout all three.** They were written
against what the code was believed to do; nothing asked whether the data was
actually there. A record with a hole in it looks exactly like a record without
one, and the broker will not sell you the missing part later.

WHAT THIS IS NOT. It is not a test. Tests run against a temporary database and
prove the code behaves; this runs against the REAL record and asks a different
question — is what we have complete? A passing suite cannot answer that, which
is precisely how eight weeks went by.

READ-ONLY BY CONSTRUCTION. The connection is opened with `mode=ro`, so a
mistake in a query here cannot damage the one irreplaceable thing this project
owns. Not a convention to remember — SQLite refuses the write.

WHAT IT DOES NOT DO. It never repairs anything. Several findings below are
expected history rather than live faults (everything before the ADR-046 and
ADR-049 fixes), and telling those apart needs a human who knows the dates. An
audit that edited the record would be a second thing that can go wrong against
the one file that cannot be replaced.

USAGE
    python scripts/audit.py                  # the cheap checks, whole history
    python scripts/audit.py --since 2026-08-20
    python scripts/audit.py --deep           # adds the option_rows checks,
                                             # which are slow; use --since

Exit codes: 0 nothing found, 1 findings, 2 the audit could not run.
"""
from __future__ import annotations

import argparse
import contextlib
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from core import session as core_session

ET = ZoneInfo("America/New_York")

# An IV at or below zero is impossible rather than merely strange. The ceiling
# is a prompt to look, not a verdict: SPX has printed above 100% in a genuine
# panic, so this catches decimal slips and division errors, not market drama.
IV_FLOOR = 0.0
IV_CEILING = 3.0

# A day is flagged short if it holds less than this fraction of the snapshots
# its own session rules call for. Not 1.0: a restart, a slow cycle or an API
# hiccup legitimately costs a few, and an audit that fires on every ordinary
# day is an audit nobody reads — the watchdog's lesson (ADR-045).
SHORT_DAY_FRACTION = 0.90

# The dates the two silent-loss bugs were fixed. Findings older than these are
# known history, not new faults, and the report says so rather than making
# Chandan remember.
FIXED_THIRD_FRIDAY = "2026-08-19"     # ADR-046
FIXED_CLOSING_PRICE = "2026-09-03"    # ADR-049


class Finding:
    """One thing that is wrong, with enough detail to act on it."""

    __slots__ = ("check", "detail", "severity", "summary")

    def __init__(self, check: str, severity: str, summary: str, detail: str = "") -> None:
        self.check = check
        self.severity = severity      # 'alarm' | 'warn' | 'note'
        self.summary = summary
        self.detail = detail


def connect(db_path: str) -> sqlite3.Connection:
    """Open the record READ-ONLY. A write from here is refused by SQLite."""
    conn = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def expected_snapshots_per_day() -> int:
    """How many snapshots a full trading day should hold, per the session rules.

    Walked from `core.session` and the configured intervals rather than written
    down again, so the expectation cannot drift from what the collector does.
    ADR-049 widened the window and this followed with no edit.
    """
    anchor = date(2026, 1, 5)     # an ordinary Monday; the rules are per-clock
    t = datetime.combine(anchor, core_session.OPEN_START, tzinfo=ET)
    end = datetime.combine(anchor, core_session.CLOSE_END, tzinfo=ET)
    total = 0
    while t < end:
        interval = core_session.expected_interval(
            core_session.session_of(t, set()),
            config.POLL_INTERVAL_EVENT, config.POLL_INTERVAL_NORMAL)
        if interval is None:
            break
        total += 1
        t += timedelta(seconds=interval)
    return total


def _is_third_friday(iso_date: str) -> bool:
    try:
        d = date.fromisoformat(iso_date)
    except (TypeError, ValueError):
        return False
    return d.weekday() == 4 and 15 <= d.day <= 21


# ─────────────────────────────────────────────────────────────────────────────
# Completeness — the bugs that got through, turned into questions
# ─────────────────────────────────────────────────────────────────────────────

def check_closing_price(conn: sqlite3.Connection, since: str | None) -> list[Finding]:
    """Does every trading day carry a price at or after the 16:00 bell?

    THE CHECK THAT WOULD HAVE CAUGHT ADR-049 ON DAY TWO. Collection ran to
    16:00 exclusive, so the last poll of every day landed at 15:59:5x and the
    close was never recorded. Ten weeks, invisible, because 126 snapshots a day
    is a complete-looking number.
    """
    rows = conn.execute(
        """
        select date(snapshot_timestamp, '-4 hours')      as d,
               max(time(snapshot_timestamp, '-4 hours')) as last_et
        from snapshots
        where status = 'COMPLETE'
          and (:since is null or date(snapshot_timestamp, '-4 hours') >= :since)
        group by d order by d
        """, {"since": since}).fetchall()

    missing = [r for r in rows if r["last_et"] < "16:00:00"]
    if not missing:
        return []

    recent = [r for r in missing if r["d"] > FIXED_CLOSING_PRICE]
    sample = ", ".join(f"{r['d']} ended {r['last_et']}" for r in missing[:3])
    severity = "alarm" if recent else "note"
    tail = (f"\n   {len(recent)} of them are AFTER the {FIXED_CLOSING_PRICE} fix "
            f"and are new faults." if recent else
            f"\n   All of them predate the {FIXED_CLOSING_PRICE} fix (ADR-049), "
            f"so this is known history, not a live fault.")
    return [Finding(
        "closing-price", severity,
        f"{len(missing)} of {len(rows)} trading days have no price at or after 16:00 ET",
        f"{missing[0]['d']} to {missing[-1]['d']}. The closing value is absent on "
        f"those days and cannot be recovered.\n   e.g. {sample}{tail}")]


def check_both_third_friday_contracts(conn: sqlite3.Connection,
                                      since: str | None) -> list[Finding]:
    """On a third-Friday expiry, are BOTH the a.m. and p.m. contracts recorded?

    THE CHECK THAT WOULD HAVE CAUGHT ADR-046 IN WEEK ONE. SPX lists two options
    for the same date and strike; the parser dropped one and the discard was
    silent. A record holding one of them looks entirely healthy.
    """
    rows = conn.execute(
        """
        select a.expiry_date                                       as expiry,
               count(distinct coalesce(a.settlement, '?'))         as kinds,
               group_concat(distinct coalesce(a.settlement, 'unlabelled')) as seen,
               max(date(s.snapshot_timestamp, '-4 hours'))         as last_seen
        from atm_iv_by_expiry a
        join snapshots s using (snapshot_id)
        where (:since is null or date(s.snapshot_timestamp, '-4 hours') >= :since)
        group by a.expiry_date
        """, {"since": since}).fetchall()

    findings = []
    for r in rows:
        if not _is_third_friday(r["expiry"]) or r["kinds"] >= 2:
            continue
        stale = r["last_seen"] <= FIXED_THIRD_FRIDAY
        findings.append(Finding(
            "third-friday-contracts", "note" if stale else "warn",
            f"third Friday {r['expiry']} holds only one contract kind: {r['seen']}",
            "SPX lists a monthly settling at the OPEN and a weekly settling at the "
            "CLOSE for this date; one is missing.\n   "
            + (f"Last seen {r['last_seen']}, before the {FIXED_THIRD_FRIDAY} fix "
               f"(ADR-046) — known history."
               if stale else
               f"Last seen {r['last_seen']}, AFTER the {FIXED_THIRD_FRIDAY} fix. "
               f"This one is a live fault.")))
    return findings


def check_day_completeness(conn: sqlite3.Connection, since: str | None) -> list[Finding]:
    """Does each trading day hold roughly the snapshots it should?

    A day far short of its own session rules means an outage. `collection_gaps`
    records the ones it detected; this catches the ones it did not, by counting
    what is actually there rather than trusting a detector.
    """
    expected = expected_snapshots_per_day()
    floor = int(expected * SHORT_DAY_FRACTION)
    rows = conn.execute(
        """
        select date(snapshot_timestamp, '-4 hours') as d, count(*) as n
        from snapshots
        where status = 'COMPLETE'
          and (:since is null or date(snapshot_timestamp, '-4 hours') >= :since)
        group by d order by d
        """, {"since": since}).fetchall()

    # Today is still filling up, and the first day of all began mid-session;
    # judging either is guaranteed noise.
    today = datetime.now(ET).date().isoformat()
    first_day = rows[0]["d"] if rows else None
    short = [r for r in rows if r["n"] < floor and r["d"] not in (today, first_day)]
    if not short:
        return []

    # A short day the collector already OWNED UP TO is not a finding — it is the
    # gap detector working. Only a day short with nothing recorded against it is
    # news, because that is data lost with nobody noticing. Reporting both the
    # same way is how an audit teaches its reader to skim it (ADR-045).
    explained = {r["d"] for r in conn.execute(
        """
        select distinct date(gap_start, '-4 hours') as d
        from collection_gaps where reason = 'COLLECTOR_OFFLINE'
        union
        select distinct date(gap_end, '-4 hours')
        from collection_gaps where reason = 'COLLECTOR_OFFLINE'
        """)}

    unexplained = sorted((r for r in short if r["d"] not in explained),
                         key=lambda r: r["n"])
    known = sorted((r for r in short if r["d"] in explained), key=lambda r: r["n"])

    findings = []
    if unexplained:
        findings.append(Finding(
            "day-completeness", "alarm",
            f"{len(unexplained)} trading days are short with NO gap recorded "
            f"against them",
            "Fewest first — "
            + ", ".join(f"{r['d']}: {r['n']}" for r in unexplained[:5])
            + f" (the rules call for {expected}).\n   Data was lost and nothing "
              f"noticed at the time, which is the failure M3.4 exists to prevent."))
    if known:
        findings.append(Finding(
            "day-completeness", "note",
            f"{len(known)} short days are explained by a recorded outage",
            "Shortest — "
            + ", ".join(f"{r['d']}: {r['n']}" for r in known[:5])
            + ".\n   The gap detector caught these at the time; listed so the "
              "count reconciles, not because they need action."))
    return findings


def check_iv_sanity(conn: sqlite3.Connection, since: str | None) -> list[Finding]:
    """Are the summarised IVs numbers a market could actually produce?"""
    row = conn.execute(
        """
        select
          sum(case when a.atm_avg_iv <= :floor then 1 else 0 end) as non_positive,
          sum(case when a.atm_avg_iv >  :ceil  then 1 else 0 end) as too_high,
          count(*)                                               as total
        from atm_iv_by_expiry a
        join snapshots s using (snapshot_id)
        where a.atm_avg_iv is not null
          and (:since is null or date(s.snapshot_timestamp, '-4 hours') >= :since)
        """, {"floor": IV_FLOOR, "ceil": IV_CEILING, "since": since}).fetchone()

    findings = []
    if row["non_positive"]:
        findings.append(Finding(
            "iv-sanity", "alarm",
            f"{row['non_positive']} of {row['total']} at-the-money IVs are at or below zero",
            "An IV of zero or less is not a market condition, it is a bad number, "
            "and anything derived from it is wrong rather than merely noisy."))
    if row["too_high"]:
        findings.append(Finding(
            "iv-sanity", "warn",
            f"{row['too_high']} of {row['total']} at-the-money IVs exceed {IV_CEILING:.0%}",
            "Possible in a genuine panic, so this is a prompt to look rather than a "
            "verdict. Check them against the underlying on those days."))
    return findings


# ─────────────────────────────────────────────────────────────────────────────
# The deep checks — option_rows is ~19M rows, so these are opt-in
# ─────────────────────────────────────────────────────────────────────────────

def check_missing_legs(conn: sqlite3.Connection, since: str | None) -> list[Finding]:
    """Does every expiry in a snapshot carry both calls and puts?

    A diagonal needs both sides. An expiry that arrived with only one is not
    half-useful, it is unusable, and it currently passes through unremarked.
    """
    rows = conn.execute(
        """
        select date(s.snapshot_timestamp, '-4 hours') as d,
               o.expiry_date                          as expiry,
               sum(o.right = 'C')                     as calls,
               sum(o.right = 'P')                     as puts
        from option_rows o
        join snapshots s using (snapshot_id)
        where (:since is null or date(s.snapshot_timestamp, '-4 hours') >= :since)
        group by s.snapshot_id, o.expiry_date
        having calls = 0 or puts = 0
        limit 200
        """, {"since": since}).fetchall()

    if not rows:
        return []
    sample = ", ".join(f"{r['d']} {r['expiry']} ({r['calls']}C/{r['puts']}P)"
                       for r in rows[:3])
    return [Finding(
        "missing-legs", "warn",
        f"{len(rows)} snapshot-expiries carry only one side of the chain"
        + (" (capped at 200)" if len(rows) == 200 else ""),
        f"e.g. {sample}.\n   A diagonal needs both sides, so these rows cannot "
        f"answer the question they were collected for.")]


def check_stale_quotes(conn: sqlite3.Connection, since: str | None) -> list[Finding]:
    """Are there at-the-money marks that never move while the market is open?

    A quote frozen across a whole session is a feed problem wearing the costume
    of a calm market. Restricted to the front expiry's at-the-money strike,
    where movement is certain, so a genuinely still deep-out-of-the-money option
    does not raise a false alarm.
    """
    rows = conn.execute(
        """
        select d, expiry, strike, right, count(*) as polls, count(distinct mark) as marks
        from (
          select date(s.snapshot_timestamp, '-4 hours') as d,
                 o.expiry_date as expiry, o.strike, o.right, o.mark
          from option_rows o
          join snapshots s using (snapshot_id)
          join atm_iv_by_expiry a
            on a.snapshot_id = o.snapshot_id
           and a.expiry_date = o.expiry_date
           and a.atm_strike  = o.strike
          where o.mark is not null
            and (:since is null or date(s.snapshot_timestamp, '-4 hours') >= :since)
        )
        group by d, expiry, strike, right
        having polls >= 20 and marks = 1
        limit 200
        """, {"since": since}).fetchall()

    if not rows:
        return []
    sample = ", ".join(f"{r['d']} {r['expiry']} {r['strike']:.0f}{r['right']} "
                       f"({r['polls']} polls, one price)" for r in rows[:3])
    return [Finding(
        "stale-quotes", "warn",
        f"{len(rows)} at-the-money contracts never changed price across a whole day"
        + (" (capped at 200)" if len(rows) == 200 else ""),
        f"e.g. {sample}.\n   At the money the price should move all day. A frozen "
        f"one is usually a feed problem, not a still market.")]


CHEAP_CHECKS = (
    check_closing_price,
    check_both_third_friday_contracts,
    check_day_completeness,
    check_iv_sanity,
)
DEEP_CHECKS = (
    check_missing_legs,
    check_stale_quotes,
)

_ICON = {"alarm": "[ALARM]", "warn": "[WARN] ", "note": "[note]  "}


def _configure_output() -> None:
    """Make stdout printable whatever it is attached to (see DEBT-039)."""
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError, OSError):
            stream.reconfigure(encoding="utf-8", errors="replace")


def run(db_path: str, since: str | None, deep: bool) -> list[Finding]:
    checks = CHEAP_CHECKS + (DEEP_CHECKS if deep else ())
    conn = connect(db_path)
    try:
        findings: list[Finding] = []
        for check in checks:
            findings.extend(check(conn, since))
        return findings
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Is the collected record complete?")
    p.add_argument("--since", metavar="YYYY-MM-DD",
                   help="only consider snapshots captured on or after this ET date")
    p.add_argument("--deep", action="store_true",
                   help="also run the option_rows checks (slow on the full history)")
    args = p.parse_args(argv)

    _configure_output()

    try:
        findings = run(config.DB_PATH, args.since, args.deep)
    except sqlite3.Error as exc:
        print(f"[ALARM] the audit could not run: {type(exc).__name__}: {exc}")
        return 2

    scope = f"since {args.since}" if args.since else "the whole history"
    print(f"Audit of {config.DB_PATH}")
    print(f"Scope: {scope}{', including the deep checks' if args.deep else ''}\n")

    if not findings:
        print("Nothing found. Every check below passed:")
        for check in CHEAP_CHECKS + (DEEP_CHECKS if args.deep else ()):
            print(f"   - {check.__name__}")
        return 0

    order = {"alarm": 0, "warn": 1, "note": 2}
    for f in sorted(findings, key=lambda f: (order[f.severity], f.check)):
        print(f"{_ICON[f.severity]} {f.summary}")
        if f.detail:
            for line in f.detail.splitlines():
                print(f"   {line}" if not line.startswith("   ") else line)
        print()

    live = [f for f in findings if f.severity in ("alarm", "warn")]
    print(f"{len(findings)} finding(s); {len(live)} need attention, "
          f"{len(findings) - len(live)} are known history.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
