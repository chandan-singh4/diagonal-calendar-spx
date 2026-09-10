"""
briefing_daemon.py — run the briefings on their own, through the session.

    python -m scripts.briefing_daemon
    python -m scripts.briefing_daemon --dry-run   # prove the schedule, no calls

**IT SLEEPS AND WAKES, IT IS NOT SCHEDULED BY WINDOWS.** That is a deliberate
copy of `collector.py`, and the reason is written down in
`scripts/register_collector_task.ps1`: Task Scheduler was tried on this machine
and rejected, because a logon trigger cannot be registered without an elevated
shell and `start_collector.bat` was blocked by Smart App Control. The working
mechanism here is a shortcut in `shell:startup`, and a process that decides its
own times fits that with no elevation, no second scheduling model to reason
about, and one place to look when a briefing does not appear.

IT ALSO MEANS THE SCHEDULE IS TESTABLE. `slots_for` is a pure function of the
day, so the times can be checked without waiting for eleven o'clock — which is
the thing a Task Scheduler XML can never offer.

WHAT IT DOES NOT DO. It does not collect, it does not write to the options
database, and it does not retry a slot it missed. A briefing describes a
moment; one delivered forty minutes late describes a moment that has gone, and
would sit in the log as though it had been read at the time. A missed slot is
recorded as missed and the daemon waits for the next one.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from core import session as core_session
from integrations import telegram
from scripts import briefing as briefing_cli

_MARKET_TZ = ZoneInfo(config.DISPLAY_TIMEZONE)

# WHEN A BRIEFING IS WORTH HAVING. Not an even grid, because the day is not
# even: the two moments the picture changes fastest are the open, where
# overnight positioning first meets real volume, and the last half hour, where
# 0DTE charm goes vertical. A fixed thirty-minute grid straddles both and
# spends its afternoon resolution on the quietest part of the session.
#
#   09:45          the first reading with real volume behind it
#   10:30 - 15:00  every thirty minutes, the slow part of the day
#   15:15 - 16:00  every fifteen, through the close
MORNING = (_dt.time(9, 45),)
HALF_HOURLY = tuple(_dt.time(h, m)
                    for h in range(10, 15) for m in (0, 30)
                    if not (h == 10 and m == 0))
CLOSING = (_dt.time(15, 0), _dt.time(15, 15), _dt.time(15, 30),
           _dt.time(15, 45), _dt.time(16, 0))

# How long after a slot's minute the daemon will still take it. A briefing is
# about a moment; past this it is describing something that has gone, and a
# late one filed under the slot's own name is worse than none, because the
# scoring log would grade it as though it had been read on time.
GRACE_SECONDS = 240

# How often the loop looks at the clock while it waits. Short enough that a
# slot is never missed by more than a few seconds, long enough to be free.
TICK_SECONDS = 20


def slots_for(day: _dt.date, holidays: set[str]) -> list[_dt.time]:
    """The briefing times for one calendar day, or none if the market is shut.

    A PURE FUNCTION OF THE DAY, which is the whole reason the schedule lives
    here rather than in a scheduler's configuration. `is_trading_day` is the
    collector's own holiday rule, reused rather than restated: a daemon that
    kept its own list of market holidays would eventually disagree with the
    process producing the data it reads, and nothing would report it.
    """
    if not core_session.is_trading_day(day, holidays):
        return []
    return sorted({*MORNING, *HALF_HOURLY, *CLOSING})


def _due(now: _dt.datetime, slot: _dt.time) -> bool:
    """Whether `slot` has arrived and has not yet gone stale."""
    at = _dt.datetime.combine(now.date(), slot, tzinfo=_MARKET_TZ)
    return 0 <= (now - at).total_seconds() <= GRACE_SECONDS


def run_slot(clock: str, previous: dict | None, *,
             dry_run: bool) -> dict | None:
    """One briefing. Returns what to carry into the next, or None on failure.

    THE CARRY IS THE RETURN VALUE rather than daemon state, so a failed slot
    cannot silently reset the chain: the caller keeps the last GOOD carry and
    the next briefing still knows what the previous one concluded, even if the
    one between them never arrived.
    """
    day = _dt.datetime.now(_MARKET_TZ).date().isoformat()
    found = briefing_cli._snapshot_at(config.DB_PATH, day, clock)
    if found is None:
        print(f"  {clock} — no snapshot at or before this time", flush=True)
        return None
    snapshot_id, taken = found

    figures = briefing_cli._figures(config.DB_PATH, snapshot_id, clock)
    prompt = (f"{briefing_cli._carry(previous)}\n\n"
              f"CURRENT FIGURES (snapshot {snapshot_id}, taken {taken})\n"
              f"{briefing_cli.json.dumps(figures, indent=2, ensure_ascii=False)}")

    print(f"\n{'=' * 72}\n  {clock}  ·  snapshot {snapshot_id}  ·  taken {taken}"
          f"\n{'=' * 72}", flush=True)

    if dry_run:
        print("  (dry run — no model called)", flush=True)
        return {"as_of": clock, "call": "(dry run)",
                "figures": briefing_cli._headline(figures)}

    try:
        answer = briefing_cli.llm.complete(briefing_cli.SYSTEM_PROMPT, prompt)
    except briefing_cli.llm.LLMError as exc:
        # ONE FAILED SLOT IS NOT A FAILED DAY. Every provider having a bad
        # minute is an ordinary event on free tiers, and the next slot builds
        # a fresh chain against rosters that may have recovered.
        print(f"  no model answered: {exc}", flush=True)
        return None

    for declined in answer.attempts:
        print(f"  (skipped {declined})", flush=True)
    print(answer.text, flush=True)

    forecast = briefing_cli._extract_forecast(answer.text)
    call = briefing_cli._extract_call(answer.text)
    briefing_cli._record({
        "session_date": day, "as_of": clock, "snapshot_id": snapshot_id,
        "taken": taken, "spot_at_briefing": figures["spot"],
        "provider": answer.provider, "model": answer.model,
        "call": call, **forecast, "text": answer.text,
    })
    print(f"\n  [{answer.provider} / {answer.model}] forecast logged: "
          f"close={forecast['close']} "
          f"range={forecast['low']}-{forecast['high']} "
          f"confidence={forecast['confidence']}\n", flush=True)

    # POSTED LAST, AFTER THE FORECAST IS ALREADY IN THE LOG. The order is the
    # whole safety of it: the log is the record and the message is a
    # convenience, so a Telegram outage costs a notification and never a
    # scored briefing. `send` reports failure by returning False rather than
    # raising, so there is nothing here that can interrupt the day.
    if telegram.configured() and not telegram.send(
            briefing_cli.for_telegram(clock, taken, figures, forecast, answer)):
        print("  (telegram post failed — the briefing is still logged)",
              flush=True)

    return {"as_of": clock, "call": call,
            "figures": briefing_cli._headline(figures)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help=
                        "walk the schedule and read the record, but call no "
                        "provider and log no forecast.")
    parser.add_argument("--show-schedule", action="store_true", help=
                        "print today's slots and exit.")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    holidays = config.MARKET_HOLIDAYS
    if args.show_schedule:
        today = _dt.datetime.now(_MARKET_TZ).date()
        slots = slots_for(today, holidays)
        print(f"{today}: " + (", ".join(s.strftime('%H:%M') for s in slots)
                              or "market closed"))
        return 0

    print(f"Briefing daemon started {_dt.datetime.now(_MARKET_TZ):%Y-%m-%d %H:%M %Z}",
          flush=True)
    # SAID ONCE, AT THE TOP. An unconfigured install is a perfectly useful
    # one — the forecast log is the record — so this is a statement of fact
    # rather than a warning, and it is not repeated at every slot.
    print("Telegram: posting to "
          f"{telegram.report_chat()}" if telegram.configured()
          else "Telegram: not configured (briefings will be logged only)",
          flush=True)

    # DONE IS KEYED ON THE DATE AS WELL AS THE TIME, so the set does not need
    # clearing at midnight — a stale entry from yesterday can never match
    # today's key, and a daemon left running over a weekend picks up on Monday
    # with no special case.
    done: set[tuple[str, str]] = set()
    carry: dict | None = None

    while True:
        now = _dt.datetime.now(_MARKET_TZ)
        for slot in slots_for(now.date(), holidays):
            key = (now.date().isoformat(), slot.strftime("%H:%M"))
            if key in done or not _due(now, slot):
                continue
            done.add(key)
            # MARKED DONE BEFORE IT RUNS, not after. A briefing that raises
            # part way through would otherwise be retried on the next tick,
            # and again, for the whole grace window — twenty attempts at the
            # thing that just failed, each one spending the free tier.
            result = run_slot(slot.strftime("%H:%M"), carry,
                              dry_run=args.dry_run)
            if result is not None:
                carry = result
        time.sleep(TICK_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
