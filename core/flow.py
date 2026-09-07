"""Contracts traded at one strike, bucketed through the session.

WHAT THIS IS, AND WHAT IT DELIBERATELY IS NOT. Chandan asked on 2026-09-06 for
gexstream's "Strike flow" panel, whose bars split four ways: calls bought, puts
bought, calls sold, puts sold. **That split cannot be built from what this
dashboard collects, and no amount of arithmetic here will produce it.** Telling
a buyer-initiated trade from a seller-initiated one means knowing, for each
individual print, whether it hit the bid or lifted the offer -- so it needs the
prints and the quote that stood at that instant. The collector snapshots the
CHAIN (bid, ask, volume, open interest, greeks) once a minute or once every
five; it never records a trade. There is no time-and-sales table in the schema.

So this module answers the honest neighbouring question: HOW MANY CONTRACTS
TRADED at this strike in each bucket, split calls from puts, with the index
level alongside. It says where and when activity landed. It does not say who
was the aggressor, and nothing downstream may label it as though it did.

VOLUME IS CUMULATIVE, WHICH IS THE WHOLE DIFFICULTY. `option_rows.volume` is
the exchange's running total for the session, not the trades since the last
poll -- so a bar is a DIFFERENCE between consecutive buckets, and every bar
depends on the two snapshots either side of it being complete. Two consequences
are baked in below, and neither is a rounding detail:

  - **The first bucket of the session is blank, not zero.** It has no
    predecessor to difference against. Its cumulative figure already contains
    every trade since the open -- printing that as "traded in this bucket"
    would put the day's opening rotation into a single bar and make 09:30 look
    like the most active minute of every session. Blank says "not known"; zero
    would say "nothing traded", and that is a different and false claim.
  - **A DECREASE is blank too.** A running total cannot fall. When it does, the
    snapshot is partial -- a poll that caught some contracts and not others --
    and the difference is an artefact of the read, not a trade count. Clamping
    it to zero would hide a data fault as a quiet market.

THE BUCKET WIDTH IS THE COLLECTOR'S OWN CADENCE, not a display choice. One
minute through the first and last half hour, five minutes midday -- which is
exactly `core.session.expected_interval`, and arrives from there rather than as
a second copy of those numbers. So every bucket holds exactly one poll and the
bars are the record itself rather than a re-bucketing of it. It also means the
width CHANGES at 10:00 and again at 15:30, so a bar's height is comparable only
to another bar of the same width -- `bucket_secs` rides on every row so the
reader can be told.

PURE, AND HANDED EVERYTHING. `core/` may not import `config`, so the timezone,
the session boundaries and the two intervals all arrive as arguments -- the
same rule, and the same reason, as core/session.py.
"""
from __future__ import annotations

from datetime import time
from zoneinfo import ZoneInfo

import pandas as pd

COLUMNS = ["bucket", "label", "call_volume", "put_volume", "total",
           "spot", "bucket_secs"]

_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _caption(stamp: pd.Timestamp) -> str:
    """"Tue 8/11 12:20 PM" -- the hover's first line.

    BUILT BY HAND rather than with strftime, because the flag that strips the
    leading zero is `%-m` on glibc and `%#m` on Windows, and the wrong one is
    emitted LITERALLY rather than raising: the label would read "Tue %-8/%-11"
    on the machine it was not written for, and only there. A caption is not
    worth a platform fork.

    FORMATTED IN PYTHON, not in the browser, for the reason DEBT-030 records:
    a timestamp formatted in the viewer's timezone is an hour wrong for one
    reader and right for everyone else, which is the kind of bug nobody
    reports because the person seeing it has no reason to doubt it.
    """
    hour = stamp.hour % 12 or 12
    part = "AM" if stamp.hour < 12 else "PM"
    return (f"{_DAYS[stamp.weekday()]} {stamp.month}/{stamp.day} "
            f"{hour}:{stamp.minute:02d} {part}")


def strike_flow(intraday: pd.DataFrame, *, strike: float, display_tz: str,
                open_end: time, midday_end: time,
                event_secs: int, normal_secs: int) -> pd.DataFrame:
    """Contracts traded at `strike` per bucket, through the session held.

    `intraday` is `load_intraday_strike_metrics`' frame: one row per snapshot
    per strike, timestamps ZONED UTC. The session is whatever that frame holds
    -- scoping is the query's job, as it is for every other function here, so
    "since the 4pm reset" is enforced once, in SQL.

    Returns one row per bucket in time order, with `call_volume`, `put_volume`
    and `total` as NULLABLE integers: NA means "not known", and the module
    docstring says when that happens and why it is not zero. `spot` is the
    index level at the bucket's last snapshot, for the price line.

    Empty in, empty out -- with columns.
    """
    needed = {"strike", "timestamp", "call_volume", "put_volume",
              "underlying_price"}
    if intraday is None or intraday.empty or not needed.issubset(intraday.columns):
        return pd.DataFrame(columns=COLUMNS)

    at = intraday[intraday["strike"] == strike]
    if at.empty:
        return pd.DataFrame(columns=COLUMNS)

    # SUM WITHIN A SNAPSHOT FIRST. A strike lists several expiries and the
    # panel draws their total; differencing the unaggregated rows would
    # difference one expiry's volume and label it as the strike's -- the same
    # trap session_range_by_strike documents.
    per_snap = (at.groupby("timestamp", as_index=False)
                  .agg(call_volume=("call_volume", "sum"),
                       put_volume=("put_volume", "sum"),
                       underlying_price=("underlying_price", "last"))
                  .sort_values("timestamp", ignore_index=True))
    # THE SORT IS BELT AND BRACES and no test can prove it necessary: groupby
    # already returns its keys in order, and removing this line leaves all 24
    # passing. It stays because the diff() below is between ADJACENT rows, so
    # an unordered frame here would not look wrong, it would BE wrong -- and
    # that is too quiet a failure to leave resting on a pandas default.

    local = per_snap["timestamp"].dt.tz_convert(ZoneInfo(display_tz))
    secs = local.dt.time.map(
        lambda t: normal_secs if open_end <= t < midday_end else event_secs)

    # FLOORED ON THE LOCAL CLOCK, because the boundaries are local ones (10:00
    # and 15:30 ET). Flooring in UTC would put the 10:00 boundary at a
    # different place in the bar sequence for half the year -- the daylight
    # saving bug that only shows up in March.
    midnight = local.dt.normalize()
    since = (local - midnight).dt.total_seconds()
    floored = midnight + pd.to_timedelta((since // secs) * secs, unit="s")

    work = per_snap.assign(bucket=floored, bucket_secs=secs.astype(int))
    # THE LAST SNAPSHOT IN THE BUCKET carries the running total for it. Not
    # the max: a running total that fell inside the bucket is a partial poll,
    # and taking the max would repair it silently.
    last = (work.groupby("bucket", as_index=False)
                .agg(call_volume=("call_volume", "last"),
                     put_volume=("put_volume", "last"),
                     spot=("underlying_price", "last"),
                     bucket_secs=("bucket_secs", "last"))
                .sort_values("bucket", ignore_index=True))

    out = pd.DataFrame({"spot": last["spot"].astype(float),
                        "bucket_secs": last["bucket_secs"].astype(int)})
    for side in ("call_volume", "put_volume"):
        # diff() leaves the first bucket NA of its own accord, which is the
        # behaviour wanted; the mask handles the impossible decrease.
        traded = last[side].astype(float).diff()
        out[side] = traded.where(traded >= 0).round().astype("Int64")
    out["total"] = out["call_volume"] + out["put_volume"]
    out["label"] = last["bucket"].map(_caption)
    out["bucket"] = last["bucket"].map(lambda s: s.isoformat())
    return out[COLUMNS]
