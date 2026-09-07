"""core/flow.py — contracts traded at one strike, bucketed through the session.

WHAT IS WORTH PINNING HERE. The arithmetic is a difference between consecutive
buckets, which sounds too simple to test until you notice that every hard case
is about what happens when the difference CANNOT be taken:

  - the first bucket, which has no predecessor;
  - a running total that fell, which is a partial poll rather than a market;
  - a strike listing several expiries, where differencing before summing gives
    one expiry's volume under the strike's name.

All three have the same failure mode: a plausible number appears where "not
known" belongs, and nobody can tell by looking. That is what these pin. The
bucket width and the label format are pinned too, because both are decisions
that were made once, deliberately, and would otherwise drift.
"""
from __future__ import annotations

from datetime import time

import pandas as pd
import pytest

from core import flow

ET = "America/New_York"
BOUNDS = dict(display_tz=ET, open_end=time(10, 0), midday_end=time(15, 30),
              event_secs=60, normal_secs=300)


def frame(rows):
    """rows: (ET wall-clock string, strike, call_vol, put_vol, spot)."""
    df = pd.DataFrame(rows, columns=["when", "strike", "call_volume",
                                     "put_volume", "underlying_price"])
    df["timestamp"] = (pd.to_datetime(df["when"]).dt.tz_localize(ET)
                                                 .dt.tz_convert("UTC"))
    return df.drop(columns=["when"])


def run(df, strike=7700.0):
    return flow.strike_flow(df, strike=strike, **BOUNDS)


# ── The difference ──────────────────────────────────────────────────────────

def test_a_bar_is_the_change_in_the_running_total_not_the_total():
    """The reason the whole module exists. `volume` is cumulative for the
    session, so a bar drawn from the raw figure would show the day so far and
    rise monotonically all afternoon."""
    out = run(frame([
        ("2026-09-04 10:00", 7700.0, 100, 40, 7700.0),
        ("2026-09-04 10:05", 7700.0, 130, 55, 7702.0),
        ("2026-09-04 10:10", 7700.0, 180, 60, 7705.0),
    ]))
    assert list(out["call_volume"].dropna()) == [30, 50]
    assert list(out["put_volume"].dropna()) == [15, 5]
    assert list(out["total"].dropna()) == [45, 55]


def test_the_first_bucket_is_blank_and_not_zero():
    """It has no predecessor. Zero would claim nothing traded; the cumulative
    figure would put the whole opening rotation into one bar."""
    out = run(frame([
        ("2026-09-04 10:00", 7700.0, 500, 300, 7700.0),
        ("2026-09-04 10:05", 7700.0, 520, 310, 7701.0),
    ]))
    assert pd.isna(out.loc[0, "call_volume"])
    assert pd.isna(out.loc[0, "put_volume"])
    assert pd.isna(out.loc[0, "total"])
    assert out.loc[1, "call_volume"] == 20


def test_a_running_total_that_fell_is_blank_and_not_clamped_to_zero():
    """A partial poll, not a quiet market. Clamping would hide a data fault as
    a real reading, which is the one outcome worse than a gap in the chart."""
    out = run(frame([
        ("2026-09-04 10:00", 7700.0, 100, 40, 7700.0),
        ("2026-09-04 10:05", 7700.0, 60, 45, 7701.0),   # impossible
        ("2026-09-04 10:10", 7700.0, 150, 50, 7702.0),
    ]))
    assert pd.isna(out.loc[1, "call_volume"])
    assert out.loc[1, "put_volume"] == 5      # the put side is unaffected
    assert pd.isna(out.loc[1, "total"])       # so the total is unknown too


def test_expiries_at_one_strike_are_summed_before_differencing():
    """Difference first and you get one expiry's volume under the strike's
    name. The two orders disagree the moment a second expiry appears."""
    out = run(frame([
        ("2026-09-04 10:00", 7700.0, 100, 0, 7700.0),
        ("2026-09-04 10:00", 7700.0, 200, 0, 7700.0),   # second expiry
        ("2026-09-04 10:05", 7700.0, 110, 0, 7701.0),
        ("2026-09-04 10:05", 7700.0, 260, 0, 7701.0),
    ]))
    assert out.loc[1, "call_volume"] == 70          # (110+260) - (100+200)


# ── The buckets ─────────────────────────────────────────────────────────────

def test_the_open_is_one_minute_a_bucket_and_midday_is_five():
    """The collector's own cadence, not a display choice, so each bucket holds
    exactly one poll."""
    out = run(frame([
        ("2026-09-04 09:31", 7700.0, 10, 0, 7700.0),
        ("2026-09-04 09:32", 7700.0, 20, 0, 7700.0),
        ("2026-09-04 09:33", 7700.0, 30, 0, 7700.0),
        ("2026-09-04 10:05", 7700.0, 40, 0, 7700.0),
        ("2026-09-04 10:10", 7700.0, 50, 0, 7700.0),
    ]))
    assert list(out["bucket_secs"]) == [60, 60, 60, 300, 300]
    assert len(out) == 5


def test_snapshots_inside_one_midday_bucket_collapse_to_its_last():
    """Not its max: a total that fell inside the bucket is a partial poll, and
    taking the max would repair it silently."""
    out = run(frame([
        ("2026-09-04 10:00", 7700.0, 100, 0, 7700.0),
        ("2026-09-04 10:05", 7700.0, 200, 0, 7701.0),
        ("2026-09-04 10:07", 7700.0, 150, 0, 7702.0),   # same 10:05 bucket
        ("2026-09-04 10:10", 7700.0, 260, 0, 7703.0),
    ]))
    assert len(out) == 3
    assert out.loc[1, "call_volume"] == 50            # 150 - 100, not 200-100
    assert out.loc[1, "spot"] == 7702.0               # the bucket's last


def test_the_close_returns_to_one_minute_buckets():
    """15:30 is a boundary in both directions; midday's width must not run on
    to the end of the day."""
    out = run(frame([
        ("2026-09-04 15:29", 7700.0, 10, 0, 7700.0),
        ("2026-09-04 15:31", 7700.0, 20, 0, 7700.0),
        ("2026-09-04 15:32", 7700.0, 30, 0, 7700.0),
    ]))
    assert list(out["bucket_secs"]) == [300, 60, 60]


# ── The caption ─────────────────────────────────────────────────────────────

def test_the_hover_caption_is_formatted_here_and_not_in_the_browser():
    """A browser formats a timestamp in the VIEWER's timezone, which is an hour
    wrong for one reader and right for everyone else (DEBT-030)."""
    out = run(frame([
        ("2026-09-04 12:20", 7700.0, 10, 0, 7700.0),
        ("2026-09-04 12:25", 7700.0, 20, 0, 7700.0),
    ]))
    assert out.loc[0, "label"] == "Fri 9/4 12:20 PM"
    assert "%" not in out.loc[0, "label"]      # the strftime platform trap


def test_midnight_and_noon_do_not_become_zero_oclock():
    assert flow._caption(pd.Timestamp("2026-09-04 00:07")) == "Fri 9/4 12:07 AM"
    assert flow._caption(pd.Timestamp("2026-09-04 12:00")) == "Fri 9/4 12:00 PM"
    assert flow._caption(pd.Timestamp("2026-09-04 13:05")) == "Fri 9/4 1:05 PM"


def test_the_bucket_is_returned_zoned_so_nothing_downstream_has_to_guess():
    out = run(frame([("2026-09-04 12:20", 7700.0, 10, 0, 7700.0)]))
    assert out.loc[0, "bucket"].startswith("2026-09-04T12:20:00-04:00")


# ── The empty and partial cases ─────────────────────────────────────────────

def test_empty_in_empty_out_with_columns():
    out = flow.strike_flow(pd.DataFrame(), strike=7700.0, **BOUNDS)
    assert list(out.columns) == flow.COLUMNS
    assert out.empty


def test_none_in_empty_out():
    assert flow.strike_flow(None, strike=7700.0, **BOUNDS).empty


def test_a_frame_missing_a_column_is_empty_rather_than_a_crash():
    df = frame([("2026-09-04 10:00", 7700.0, 10, 0, 7700.0)])
    assert flow.strike_flow(df.drop(columns=["underlying_price"]),
                            strike=7700.0, **BOUNDS).empty


def test_a_strike_that_never_traded_is_empty_rather_than_the_whole_board():
    """The filter has to happen, and it has to happen first. Returning the
    board's flow under one strike's heading is the worst available answer."""
    df = frame([("2026-09-04 10:00", 7700.0, 10, 0, 7700.0)])
    assert flow.strike_flow(df, strike=7725.0, **BOUNDS).empty


def test_other_strikes_are_excluded_from_the_chosen_one():
    out = run(frame([
        ("2026-09-04 10:00", 7700.0, 100, 0, 7700.0),
        ("2026-09-04 10:00", 7725.0, 900, 0, 7700.0),
        ("2026-09-04 10:05", 7700.0, 120, 0, 7701.0),
        ("2026-09-04 10:05", 7725.0, 999, 0, 7701.0),
    ]))
    assert out.loc[1, "call_volume"] == 20


def test_rows_come_back_in_time_order_whatever_order_they_arrived_in():
    """The difference is taken between ADJACENT rows, so an unsorted frame
    would not merely look wrong — it would compute wrong."""
    out = run(frame([
        ("2026-09-04 10:10", 7700.0, 180, 0, 7702.0),
        ("2026-09-04 10:00", 7700.0, 100, 0, 7700.0),
        ("2026-09-04 10:05", 7700.0, 130, 0, 7701.0),
    ]))
    assert list(out["label"]) == ["Fri 9/4 10:00 AM", "Fri 9/4 10:05 AM",
                                 "Fri 9/4 10:10 AM"]
    assert list(out["call_volume"].dropna()) == [30, 50]


def test_the_counts_are_integers_and_nullable():
    """A float count prints as "30.0" and an object column will not compare;
    Int64 is what carries both a whole number and a missing one."""
    out = run(frame([
        ("2026-09-04 10:00", 7700.0, 100, 40, 7700.0),
        ("2026-09-04 10:05", 7700.0, 130, 55, 7701.0),
    ]))
    for col in ("call_volume", "put_volume", "total"):
        assert str(out[col].dtype) == "Int64"


@pytest.mark.parametrize("column", flow.COLUMNS)
def test_every_promised_column_is_present(column):
    out = run(frame([("2026-09-04 10:00", 7700.0, 10, 0, 7700.0)]))
    assert column in out.columns
