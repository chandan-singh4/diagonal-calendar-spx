"""
What clock the dashboard shows — pinned, so DEBT-030 cannot be "fixed" silently.

WHY THIS FILE EXISTS. `dataaccess/queries.py` converts stored UTC to New York and
then STRIPS the zone, leaving a naive wall-clock time, because Plotly's
rangebreaks require one. That is a display decision taken inside the read layer,
and DEBT-030 is the plan to move it out.

The danger is not the change. It is that **nothing would have caught it.** The
golden files were captured against the current behaviour, so shifting every
chart by four hours produces output that matches its own recorded reference and
passes. A refactor could move every timestamp on every chart and the suite would
stay green.

So these two tests assert the behaviour DIRECTLY, from a known stored value, and
compute the expected answer INDEPENDENTLY of the code under test — via
`zoneinfo`, not `config.DISPLAY_TIMEZONE`. They will fail loudly when DEBT-030 is
done. **That is the intended outcome, not a regression:** whoever does it must
update these two tests and the ten chart sites in the same breath, which is
exactly the coupling that makes the change safe.

Both tests are characterization, not correctness. Nothing here says a naive
wall-clock is the right thing to return. It says it is the CURRENT thing.
"""
from __future__ import annotations

from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from conftest import MC_FRONT_EXPIRY, make_atm_iv_history

from dataaccess import queries

pytestmark = pytest.mark.integration

# Written out rather than read from config on purpose: if this test took the zone
# from the same constant the code uses, it could only ever agree with itself.
NEW_YORK = ZoneInfo("America/New_York")


def test_timestamps_come_back_with_no_timezone_attached(temp_db):
    """Naive is the current contract. Every chart depends on it (rangebreaks
    misplace points otherwise) and so does anything doing timestamp arithmetic
    against another naive value."""
    make_atm_iv_history(temp_db, [0.18, 0.19, 0.20])

    df = queries.load_atm_hist(temp_db, MC_FRONT_EXPIRY, 1)

    assert not df.empty, "fixture wrote no rows the query could see"
    assert df["timestamp"].dt.tz is None, (
        "timestamps came back timezone-aware. If this is the DEBT-030 fix, the "
        "ten chart sites in app.py need .dt.tz_localize(None) applied at draw "
        "time in the same change — see ADR-034."
    )


def test_the_wall_clock_is_new_york_and_not_the_stored_utc(temp_db):
    """The stored value is UTC; the returned value must be the same instant
    expressed in New York. Computed here with zoneinfo, so the assertion does
    not depend on the code it is checking."""
    written = make_atm_iv_history(temp_db, [0.18, 0.19])

    df = queries.load_atm_hist(temp_db, MC_FRONT_EXPIRY, 1)

    expected = sorted(
        pd.Timestamp(ts, tz="UTC").tz_convert(NEW_YORK).tz_localize(None)
        for ts in written
    )
    assert sorted(df["timestamp"].tolist()) == expected

    # And prove a conversion actually happened, rather than UTC being handed
    # back with its label removed. New York is never at UTC+0.
    stored_utc = pd.Timestamp(written[-1])
    assert df["timestamp"].max() != stored_utc, (
        "the returned wall-clock equals the stored UTC — the conversion to "
        f"{NEW_YORK} was skipped"
    )
