"""core/series.py -- the reshaping both screens share.

Everything here was extracted from `views/edge.py` so the read-only API could
serve the Calendar Edge tab without importing a page. Each function is now
called from two places, which is the point and also the risk: a change here
moves the Streamlit chart and the served one together, and these checks are
what say whether it moved them correctly.
"""
from __future__ import annotations

import pandas as pd
import pytest

from core.series import (
    market_open_lines,
    merge_atm_pair,
    ratio_bands,
    strike_crossings,
)


# ── merge_atm_pair ──────────────────────────────────────────────────────────

def _series(stamps, values):
    return pd.DataFrame({"timestamp": pd.to_datetime(stamps, utc=True),
                         "atm_iv": values})


def test_only_timestamps_observed_on_BOTH_sides_can_carry_a_ratio():
    """The join is inner, and that is the whole safety of this function.

    Two expiries are polled independently and either can miss a snapshot. An
    outer join -- or a merge by position -- would divide a front reading by a
    back reading from a DIFFERENT MINUTE and hand back a ratio that reads
    perfectly and never existed.
    """
    front = _series(["2026-09-04T13:30Z", "2026-09-04T13:31Z"], [20.0, 21.0])
    back = _series(["2026-09-04T13:31Z"], [10.5])

    out = merge_atm_pair(front, back, "America/New_York")

    assert len(out) == 1
    assert out["iv_ratio"].iloc[0] == 2.0
    assert out["timestamp"].iloc[0] == pd.Timestamp("2026-09-04 09:31:00")


def test_one_empty_side_gives_nothing_rather_than_half_a_ratio():
    front = _series(["2026-09-04T13:30Z"], [20.0])
    assert merge_atm_pair(front, pd.DataFrame(), "America/New_York").empty
    assert merge_atm_pair(pd.DataFrame(), front, "America/New_York").empty


def test_the_ratio_is_front_over_back():
    """Above 1.00 the front is richer than the back, which is the condition
    the whole strategy waits for. Inverted, every regime label would be the
    opposite of the truth and the chart would still look normal."""
    front = _series(["2026-09-04T13:30Z"], [26.0])
    back = _series(["2026-09-04T13:30Z"], [20.0])
    assert merge_atm_pair(front, back, "America/New_York")["iv_ratio"].iloc[0] == 1.3


# ── strike_crossings ────────────────────────────────────────────────────────

def test_a_crossing_is_directed():
    out = strike_crossings([1, 2, 3], [99.0, 101.0, 99.0], [100.0])
    assert [p["x"] for p in out["up"]] == [2]
    assert [p["x"] for p in out["down"]] == [3]


def test_a_price_resting_on_the_strike_is_not_a_crossing_every_minute():
    """The dangerous version. With both inequalities open, a price that sits
    exactly at the strike reports a crossing on every subsequent reading, and
    the chart fills with events that never happened -- which reads as a
    volatile session rather than as a bug."""
    out = strike_crossings([1, 2, 3, 4], [100.0] * 4, [100.0])
    assert out["up"] == [] and out["down"] == []


def test_a_session_gap_is_not_a_crossing():
    """`break_sessions` inserts NaN rows. A weekend must not be reported as
    the market crossing a strike."""
    out = strike_crossings([1, 2, 3], [99.0, float("nan"), 101.0], [100.0])
    assert out["up"] == [] and out["down"] == []


def test_every_strike_is_checked():
    out = strike_crossings([1, 2], [99.0, 111.0], [100.0, 110.0])
    assert sorted(p["y"] for p in out["up"]) == [100.0, 110.0]


# ── ratio_bands ─────────────────────────────────────────────────────────────

def test_the_open_ends_are_stated_as_absent_rather_than_as_infinity():
    """JSON has no infinity. Python's json module writes the bare token
    `Infinity`, which is not valid JSON -- some parsers take it, others
    refuse, and the failure would appear in a browser rather than here."""
    import json

    bands = ratio_bands()
    assert bands[0]["high"] is None
    assert bands[-1]["low"] is None
    assert "Infinity" not in json.dumps(bands)


# ─────────────────────────────────────────────────────────────────────────────
# The intraday scatter's two axes
# ─────────────────────────────────────────────────────────────────────────────

def test_hour_of_day_puts_half_past_ten_at_ten_point_five():
    """The colour axis of the scatter. Decimal hours, not clock strings."""
    import pandas as pd

    from core.series import hour_of_day

    df = pd.DataFrame({"timestamp": pd.to_datetime(
        ["2026-09-04 09:30:00", "2026-09-04 10:30:00", "2026-09-04 15:45:00"])})
    assert list(hour_of_day(df)) == [9.5, 10.5, 15.75]


def test_a_gap_row_has_no_hour():
    """`break_sessions` inserts NaT rows; a weekend must not colour as midnight."""
    import pandas as pd

    from core.series import hour_of_day

    df = pd.DataFrame({"timestamp": pd.to_datetime(
        ["2026-09-04 09:30:00", None])})
    out = hour_of_day(df)
    assert out.iloc[0] == 9.5
    assert pd.isna(out.iloc[1])


def test_the_scatter_domain_spans_both_series_not_one():
    """One range for both axes, or the R=1 diagonal is not at 45 degrees and
    every dot's position relative to it becomes a drawing artefact."""
    import pandas as pd

    from core.series import scatter_domain

    df = pd.DataFrame({"front_iv": [10.0, 20.0], "back_iv": [5.0, 12.0]})
    lo, hi = scatter_domain(df)
    # Spans 5 to 20 -- the back's low and the front's high -- plus 5% padding.
    assert lo == pytest.approx(5.0 - 0.75)
    assert hi == pytest.approx(20.0 + 0.75)


def test_a_flat_frame_still_gets_a_drawable_range():
    """Every reading identical is a real state on a quiet contract, and a
    zero-width range is one Plotly cannot draw."""
    import pandas as pd

    from core.series import scatter_domain

    df = pd.DataFrame({"front_iv": [12.0, 12.0], "back_iv": [12.0, 12.0]})
    lo, hi = scatter_domain(df)
    assert (lo, hi) == (11.0, 13.0)


# ── market_open_lines (Calendar Edge day dividers, 2026-09-06) ──────────────

def test_a_single_day_gets_no_open_marker_because_one_marker_is_no_marker():
    """The line exists to say WHERE ONE DAY ENDS AND THE NEXT BEGINS.

    On a one-day window there is no such boundary to mark, and a lone rule at
    09:30 would read as a session divider that divides nothing -- or worse, as
    a boundary the reader then goes looking for the other side of.
    """
    stamps = pd.to_datetime(["2026-09-04 09:35", "2026-09-04 15:55"])
    assert market_open_lines(stamps) == []


def test_one_marker_per_TRADING_DAY_PRESENT_not_per_calendar_day():
    """A weekend gap must not produce two lines for days with no data.

    The dates come from the readings themselves, so a Friday-to-Monday window
    draws two lines and not four. Generating a line per calendar day between
    the ends would put a 09:30 rule in the middle of a range break, where
    there is no axis to hang it on.
    """
    stamps = pd.to_datetime([
        "2026-09-04 09:35", "2026-09-04 15:55",     # Friday
        "2026-09-08 09:31", "2026-09-08 10:02",     # Tuesday, after a holiday
    ])
    assert market_open_lines(stamps) == [
        "2026-09-04T09:30:00", "2026-09-08T09:30:00"]


def test_an_empty_window_asks_for_no_lines_rather_than_raising():
    """A pair with no overlapping readings is a real state, not a fault."""
    assert market_open_lines([]) == []
    assert market_open_lines(None) == []
