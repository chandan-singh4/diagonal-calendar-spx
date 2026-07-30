"""
Tests for break_sessions() — the line-breaking helper behind BUG-002.

WHY THIS MODULE: charts in app.py are drawn from rows that are contiguous in
the DATA but not in TIME. Between Friday's close and Monday's open there are no
points, so Plotly joins the last Friday point to the first Monday point with a
straight line — inventing a smooth IV move across a period when the market was
shut. break_sessions() inserts a NaN row into the gap so the line breaks
instead.

ADR-006 describes the pairing: `SESSION_RANGEBREAKS` collapses the empty axis
SPACE, `break_sessions()` breaks the LINE across it. They are complementary,
and the rangebreak alone does not stop the connector being drawn.

BUG-002 was that the helper was wired into two of the three chart frames and
not into the Selected-Strike IV chart's call/put frames. That is fixed; these
tests cover the helper itself, which had no tests despite being the mechanism
the fix depends on, plus a guard that the wiring cannot be silently removed
again.

The helper is loaded via tests/app_loader.py rather than imported, because
importing app.py runs a Streamlit page against the production database.
"""
from __future__ import annotations

import pandas as pd
import pytest
from app_loader import APP_PATH, definition_sources, load_scanner_functions


@pytest.fixture(scope="module")
def break_sessions():
    return load_scanner_functions()["break_sessions"]


def frame(*stamps: str, value_col: str = "iv") -> pd.DataFrame:
    return pd.DataFrame({
        "timestamp": pd.to_datetime(list(stamps)),
        value_col: list(range(len(stamps))),
    })


# ─────────────────────────────────────────────────────────────────────────────
# The behaviour BUG-002 depends on
# ─────────────────────────────────────────────────────────────────────────────

def test_a_weekend_gap_gets_a_breaker_row(break_sessions):
    """Friday 15:59 to Monday 09:30 — the line must not cross it."""
    df = frame("2026-07-24 15:55", "2026-07-24 15:59",
               "2026-07-27 09:30", "2026-07-27 09:35")
    out = break_sessions(df)
    assert len(out) == len(df) + 1


def test_a_holiday_gap_gets_a_breaker_row(break_sessions):
    """2026-07-03 is Independence Day observed — the first holiday inside the
    collected range, and the case named in BUG-002."""
    df = frame("2026-07-02 15:59", "2026-07-06 09:30")
    out = break_sessions(df)
    assert len(out) == 3


def test_the_breaker_row_carries_nan_in_every_value_column(break_sessions):
    """This is what actually breaks the line. A breaker row that carried a
    number would draw the connector it was meant to prevent."""
    df = frame("2026-07-24 15:59", "2026-07-27 09:30")
    out = break_sessions(df)
    inserted = out[out["iv"].isna()]
    assert len(inserted) == 1


def test_the_breaker_lands_inside_the_gap_not_outside_it(break_sessions):
    """One minute after the last real point, so it sorts between the two and
    the break appears in the right place."""
    df = frame("2026-07-24 15:59", "2026-07-27 09:30")
    out = break_sessions(df).sort_values("timestamp").reset_index(drop=True)
    assert out.loc[1, "timestamp"] == pd.Timestamp("2026-07-24 16:00")
    assert out["timestamp"].is_monotonic_increasing


def test_a_ratio_column_is_broken_too(break_sessions):
    """The Selected-Strike chart plots a ratio on a second axis from the same
    frame. It must break at the same place, or the ratio line crosses the
    holiday while the IV lines do not."""
    df = frame("2026-07-02 15:59", "2026-07-06 09:30")
    df["call_ratio"] = [1.10, 1.20]
    out = break_sessions(df)
    assert out["call_ratio"].isna().sum() == 1


def test_normal_intraday_points_are_untouched(break_sessions):
    """Five-minute MIDDAY cadence is not a gap. Breaking there would shatter
    every line into disconnected fragments."""
    df = frame("2026-07-21 11:00", "2026-07-21 11:05",
               "2026-07-21 11:10", "2026-07-21 11:15")
    out = break_sessions(df)
    assert len(out) == len(df)
    assert not out["iv"].isna().any()


def test_an_overnight_gap_is_also_broken(break_sessions):
    """Not only holidays: an ordinary night is 17.5 hours of no trading, and a
    connector across it is just as false."""
    df = frame("2026-07-21 15:59", "2026-07-22 09:30")
    assert len(break_sessions(df)) == 3


def test_multiple_gaps_each_get_their_own_break(break_sessions):
    """A 20-day window spans several weekends."""
    df = frame("2026-07-17 15:59", "2026-07-20 09:30",
               "2026-07-24 15:59", "2026-07-27 09:30")
    out = break_sessions(df)
    assert out["iv"].isna().sum() == 3


@pytest.mark.parametrize("rows", [0, 1])
def test_a_frame_too_short_to_have_a_gap_is_returned_unchanged(break_sessions, rows):
    df = frame(*["2026-07-21 11:00"][:rows])
    assert len(break_sessions(df)) == rows


def test_a_frame_without_the_timestamp_column_is_returned_unchanged(break_sessions):
    df = pd.DataFrame({"other": [1, 2, 3]})
    assert break_sessions(df).equals(df)


def test_the_gap_threshold_is_configurable(break_sessions):
    """Default 60 minutes. The parameter exists so a chart with a coarser
    cadence can raise it rather than break on its own normal spacing."""
    df = frame("2026-07-21 11:00", "2026-07-21 12:30")     # 90 minutes
    assert len(break_sessions(df)) == 3
    assert len(break_sessions(df, max_gap_minutes=120)) == 2


# ─────────────────────────────────────────────────────────────────────────────
# The wiring itself — BUG-002 was a missing CALL, not a broken function
#
# The helper was correct all along; it simply was not applied to two of the
# frames. A unit test of the helper would have stayed green throughout the
# bug's entire life, so the wiring needs its own guard.
# ─────────────────────────────────────────────────────────────────────────────

def _selected_strike_source() -> str:
    """The source of the Selected-Strike IV chart block.

    REPOINTED in M2 step 2.4 — from app.py to views/strike.py. The docstring
    below said extracting this block was M2 work; it has now happened, and
    these tests failed on the missing anchor, which is what the anchor is
    for.

    The 4,000-character window is KEPT rather than widened to the whole
    module, even though the module is now just this one tab. Widening it
    would make every assertion here easier to satisfy — `in src` searches a
    larger haystack, and the ordering test's `.index()` could match a
    different occurrence — and a re-point is not the place to loosen a
    check. All four anchors were verified unique in the new file, and absent
    from app.py, before this was changed.
    """
    src = (APP_PATH.parent / "views" / "strike.py").read_text(encoding="utf-8")
    start = src.index('<span class="sh-ttl">Selected-Strike IV</span>')
    return src[start:start + 4000]


@pytest.mark.parametrize("frame_name", ["cm", "pm"])
def test_the_selected_strike_frames_are_passed_through_break_sessions(frame_name):
    """FIXED — BUG-002. Both call (cm) and put (pm) frames must be broken.

    Asserted against the source because the chart block is inline Streamlit
    page code with no seam to call into; extracting it is M2 work. Crude, but
    it fails loudly if the call is dropped, which is exactly how this bug got
    in.
    """
    src = _selected_strike_source()
    assert f"{frame_name} = break_sessions({frame_name})" in src, (
        f"the {frame_name} frame is no longer passed through break_sessions "
        f"-- BUG-002 has regressed and the chart will draw connectors across "
        f"holidays again"
    )


def test_break_sessions_is_applied_after_the_ratio_is_computed():
    """Order matters. break_sessions() only copies the timestamp column into
    the breaker row, so every other column comes out NaN — which is what breaks
    the line. Computing the ratio AFTER the break would fill that NaN with a
    real number (or an inf) and the ratio trace would cross the holiday even
    though the IV traces did not.
    """
    src = _selected_strike_source()
    for frame_name, ratio in (("cm", "call_ratio"), ("pm", "put_ratio")):
        ratio_at = src.index(f'{frame_name}["{ratio}"] =')
        break_at = src.index(f"{frame_name} = break_sessions({frame_name})")
        assert ratio_at < break_at, f"{ratio} must be computed before the break"


def test_the_statistics_frame_is_deliberately_not_broken():
    """The Historical Statistics tab builds its own `pm` frame and feeds it to
    range_stats/percentile_rank — numbers, not a line. Inserting NaN breaker
    rows there would corrupt min/max and the percentile rank.

    Pinned so that a later 'consistency' cleanup does not helpfully add the
    call. The absence is the correct behaviour, and it is not obvious.

    REPOINTED in M2 step 2.4, the same way ADR-032 repointed the test below:
    the tab moved to views/historical.py and this failed on its own anchor,
    which is what the anchor is for. The window search is gone with it — the
    whole module is now that one tab, so the assertion covers the file rather
    than 2,000 characters after a heading, and can no longer be satisfied by
    a `break_sessions` call sitting just past the cut-off.
    """
    src = (APP_PATH.parent / "views" / "historical.py").read_text(encoding="utf-8")
    assert 'pm["ratio"] = pm["f"] / pm["b"]' in src, "anchor moved; re-point this test"
    assert "break_sessions" not in src, (
        "the statistics frame must NOT be broken -- NaN rows would corrupt "
        "range_stats() and percentile_rank()"
    )


def test_break_sessions_is_defined_once_where_the_loader_expects_it():
    """Guards the AST loader: if break_sessions moves again, fail here with a
    clear reason rather than mysteriously.

    REPOINTED in M2 (ADR-032). It used to assert app.py defined the function;
    the extraction moved it to core/charts.py and this test did its job by
    failing. Asserting exactly one home also catches the worse case — a copy
    left behind in app.py, where the dashboard would run one version and the
    golden tests would measure the other.
    """
    assert definition_sources("break_sessions") == ["core/charts.py"]
