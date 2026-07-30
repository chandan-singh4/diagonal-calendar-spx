"""
What clock the dashboard shows — pinned at both ends of the DEBT-030 fix.

THIS FILE DID ITS JOB. It was written (ADR-034) to make DEBT-030 impossible to
"fix" silently. The golden fixtures were captured against the old behaviour, so
shifting every chart by four hours would have matched its own recorded reference
and passed; these tests asserted the behaviour directly instead, and when the
fix landed on 2026-07-30 they failed exactly as intended. That failure is what
forced the ten chart sites to be dealt with in the same change.

WHAT IT GUARDS NOW. The same question, moved one step down the pipeline. Before
the fix the read layer returned naive Eastern wall-clock. It now returns zoned
UTC, and `core.charts.to_display_time` converts at the point of drawing. So:

  * the READ must hand back the stored zone and take no display decision
  * the CHART must still receive Eastern wall-clock, exactly as before

The second assertion is the one that matters. It is the whole reason the fix is
safe: had a chart site been missed, its x-axis would have silently moved four or
five hours, every session break would have landed in the wrong place, and the
picture would still have looked entirely plausible.

Expected values are still computed with `zoneinfo` rather than from
`config.DISPLAY_TIMEZONE`, so these tests cannot merely agree with the code they
are checking.

Characterization, not correctness: nothing here argues UTC-in-the-read is
morally right. It says it is the contract, and that the pixels did not move.
"""
from __future__ import annotations

from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from conftest import MC_CALL_STRIKE, MC_FRONT_EXPIRY, make_atm_iv_history, make_transform_history

import config
from core.charts import to_display_time
from dataaccess import queries

pytestmark = pytest.mark.integration

# Written out rather than read from config on purpose: if this test took the zone
# from the same constant the code uses, it could only ever agree with itself.
NEW_YORK = ZoneInfo("America/New_York")


# ─────────────────────────────────────────────────────────────────────────────
# The read layer
# ─────────────────────────────────────────────────────────────────────────────

def test_the_read_layer_hands_back_the_stored_zone(temp_db):
    """DEBT-030's actual fix. A read returns an unambiguous instant; what a
    human should SEE is not its business. Anything else reading this data —
    M4's data service, M7's models — gets a time that means one thing."""
    make_atm_iv_history(temp_db, [0.18, 0.19, 0.20])

    df = queries.load_atm_hist(temp_db, MC_FRONT_EXPIRY, 1)

    assert not df.empty, "fixture wrote no rows the query could see"
    assert df["timestamp"].dt.tz is not None, (
        "timestamps came back naive. That is the pre-DEBT-030 behaviour: the "
        "read layer deciding what the chart wants. Return the stored zone and "
        "let core.charts.to_display_time convert at draw time."
    )
    assert str(df["timestamp"].dt.tz) == "UTC"


def test_the_read_layer_does_not_shift_the_instant(temp_db):
    """Zoned is not enough — it has to be the SAME moment that was stored.
    A conversion that relabelled rather than converted would satisfy the test
    above and be four hours wrong."""
    written = make_atm_iv_history(temp_db, [0.18, 0.19])

    df = queries.load_atm_hist(temp_db, MC_FRONT_EXPIRY, 1)

    expected = sorted(pd.Timestamp(ts, tz="UTC") for ts in written)
    assert sorted(df["timestamp"].tolist()) == expected


# ─────────────────────────────────────────────────────────────────────────────
# The draw layer — the half that must NOT have changed
# ─────────────────────────────────────────────────────────────────────────────

def test_what_a_chart_receives_is_still_new_york_wall_clock(temp_db):
    """The equivalence that makes DEBT-030 safe.

    This is deliberately the same assertion the old version of this file made
    about `load_atm_hist` directly. The conversion moved; the answer must not.
    If this passes, every chart is plotting the same x values it plotted
    before the read layer changed.
    """
    written = make_atm_iv_history(temp_db, [0.18, 0.19])

    df = to_display_time(
        queries.load_atm_hist(temp_db, MC_FRONT_EXPIRY, 1),
        config.DISPLAY_TIMEZONE,
    )

    assert df["timestamp"].dt.tz is None, (
        "Plotly's rangebreaks mis-place points on zoned timestamps — this is "
        "the one place the zone is legitimately stripped"
    )
    expected = sorted(
        pd.Timestamp(ts, tz="UTC").tz_convert(NEW_YORK).tz_localize(None)
        for ts in written
    )
    assert sorted(df["timestamp"].tolist()) == expected

    # And prove a conversion actually happened, rather than UTC being handed
    # on with its label removed. New York is never at UTC+0.
    stored_utc = pd.Timestamp(written[-1])
    assert df["timestamp"].max() != stored_utc, (
        f"the chart's wall-clock equals the stored UTC — the conversion to "
        f"{NEW_YORK} was skipped"
    )


def test_to_display_time_does_not_mutate_the_memoised_frame(temp_db):
    """These frames come out of a Streamlit @st.cache_data memo, and the same
    object is handed to every later reader. Converting one in place would
    corrupt the cache — the second tab to ask for it would get a frame that
    had already been localised, and localising a naive value raises."""
    make_atm_iv_history(temp_db, [0.18, 0.19])
    df = queries.load_atm_hist(temp_db, MC_FRONT_EXPIRY, 1)

    before = df["timestamp"].tolist()
    to_display_time(df, config.DISPLAY_TIMEZONE)

    assert df["timestamp"].tolist() == before, "the source frame was mutated"
    assert df["timestamp"].dt.tz is not None


def test_the_today_filter_still_cuts_on_the_trading_day(temp_db):
    """The trap this fix had to step around, tested where it actually bites.

    `load_contract_hist(days=1)` keeps only the last calendar date, and that
    date is a TRADING-session question, so it must be asked in market time.
    A 20:05 New York row is already tomorrow in UTC, so comparing UTC dates
    splits one evening session across two and keeps only its tail.

    THE FIRST VERSION OF THIS TEST COULD NOT SEE THAT. It wrote three rows
    five minutes apart at whatever time the suite happened to run, which sit
    on one UTC date almost always — so the broken and correct filters agreed
    and the mutation survived. Same lesson as ADR-029: an example too simple
    to exhibit the fault is not a test of it. This version places the rows
    either side of the boundary ON PURPOSE, at 19:55 and 20:05 New York,
    which are 23:55 and 00:05 UTC — one New York date, two UTC dates.

    Correct (market time): both rows survive.
    Broken  (UTC dates):   only the 00:05 row survives.
    """
    now_utc = pd.Timestamp.now(tz="UTC")
    # The most recent 20:05 in New York, which is always in the past.
    ny_now = now_utc.tz_convert(NEW_YORK)
    target = ny_now.normalize() + pd.Timedelta(hours=20, minutes=5)
    if target > ny_now:
        target -= pd.Timedelta(days=1)
    end_minutes_ago = int((now_utc - target.tz_convert("UTC")).total_seconds() // 60)

    written = make_transform_history(
        temp_db, [6.0, 6.0], interval_minutes=10, end_minutes_ago=end_minutes_ago,
    )
    utc_dates = {pd.Timestamp(t).date() for t in written}
    assert len(utc_dates) == 2, (
        f"fixture did not straddle UTC midnight ({utc_dates}); this test "
        f"cannot distinguish the two filters without that"
    )

    df = queries.load_contract_hist(temp_db, MC_FRONT_EXPIRY, MC_CALL_STRIKE, "CALL", 1)
    if df.empty:
        pytest.skip("fixture produced no contract rows for this expiry/strike")

    local_dates = set(df["timestamp"].dt.tz_convert(NEW_YORK).dt.date)
    assert len(local_dates) == 1, (
        f"'today' returned {len(local_dates)} New York dates: {local_dates}"
    )
    assert len(df) == 2, (
        f"'today' kept {len(df)} of 2 rows from a single New York evening. "
        f"The filter is cutting on UTC dates, so the session was split at "
        f"20:00 New York and only the part after it survived."
    )
