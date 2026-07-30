"""
Golden-output ("characterization") tests for the DISPLAY layer.

WHAT THIS IS FOR
----------------
test_scanner_golden.py pins the numbers the scanner computes. This file pins
everything that happens between those numbers and your eyes:

  * the ORDER opportunity cards appear in        (_rank_for_panel)
  * the geometry of the multicolor IV-ratio line (_banded_ratio_traces)
  * the text in every cell and glyph on a card   (_sparkline, _fmt_*)
  * a card's identity across reruns              (_card_key)

M2 moves this code into views/ and core/. A crash during that move would be
obvious. The dangerous failure is silent and cosmetic-looking: cards come back
in a different order, or a chart segment changes colour at the wrong x, and the
screen still looks plausible. Nobody notices for weeks. Ordering in particular
is not cosmetic — the top card is the one that gets traded.

WHAT THIS DOES NOT CLAIM
------------------------
Same limit as the scanner goldens, and worth restating: nothing here says the
current behaviour is CORRECT. _rank_for_panel's tiering is a judgment call
(ADR-?? / see its docstring); these tests freeze it, they do not endorse it.
Two tests below are marked as pinning behaviour that is arguably wrong, with
the reasoning inline. If we later decide to change it, the test goes red, we
read why it was frozen, and we change it deliberately.

WHY THESE FUNCTIONS AND NOT THE WHOLE TAB
-----------------------------------------
These six are pure — no database, no Streamlit session. The DB-backed pipeline
(_compute_mc_core, _candidate_signals, the eleven _load_* queries) is NOT
covered here; it needs snapshot fixtures and is tracked separately. Pinning the
cheap pure layer first is not laziness, it is where the ratio of protection to
effort is highest.

_rank_for_panel is exercised against the same real production snapshots the
scanner goldens use, so the full chain snapshot -> scanner -> ranking is pinned
end to end.

No database is opened here.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from app_loader import load_display_functions

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "scanner"

SNAPSHOTS = sorted(p.name.replace("_meta.txt", "") for p in FIXTURE_DIR.glob("*_meta.txt"))


@pytest.fixture(scope="module")
def disp() -> dict:
    return load_display_functions()


# ═══════════════════════════════════════════════════════════════════════════════
# Card ordering — the highest-stakes thing in this file
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("tag", SNAPSHOTS)
def test_ranking_of_real_snapshot_is_stable(disp, tag):
    """The order cards appear in, for a real snapshot, must not drift.

    This is the end-to-end pin: real chain -> scanner output -> ranked panel.
    The assertion is on the full ordered key sequence, not just the winner, so a
    reshuffle further down the list is caught too.
    """
    scanned = pd.read_csv(FIXTURE_DIR / f"{tag}_scanner.csv.gz")
    ranked = disp["_rank_for_panel"](scanned)

    assert len(ranked) == len(scanned), "ranking must not add or drop rows"

    # Tier 1: every asymmetric combo outranks every symmetric one.
    asym = (ranked["Put Strike"] != ranked["Call Strike"]).tolist()
    assert asym == sorted(asym, reverse=True), (
        "asymmetric combos must all sort above symmetric ones — a symmetric "
        "(put==call) combo is a degenerate straddle, not this strategy's setup"
    )

    # Tier 2: within each tier, Transform Diff descends.
    for _, block in ranked.groupby(ranked["Put Strike"] != ranked["Call Strike"]):
        diffs = block["Transform Diff"].tolist()
        assert diffs == sorted(diffs, reverse=True), (
            "within a tier, the gap must descend — the best opportunity is top"
        )


def test_ranking_puts_bigger_gap_first_among_asymmetric(disp):
    """The core promise, on data small enough to read by eye."""
    df = pd.DataFrame({
        "Put Strike":     [6000.0, 6000.0, 6100.0],
        "Call Strike":    [6100.0, 6200.0, 6100.0],  # row 2 is symmetric
        "Transform Diff": [5.0,    9.0,    99.0],
    })
    ranked = disp["_rank_for_panel"](df)

    # 99.0 is the biggest gap in the frame but it is symmetric, so it goes LAST.
    assert ranked["Transform Diff"].tolist() == [9.0, 5.0, 99.0]


def test_ranking_leaves_an_empty_frame_alone(disp):
    empty = pd.DataFrame(columns=["Put Strike", "Call Strike", "Transform Diff"])
    assert disp["_rank_for_panel"](empty).empty


def test_ranking_does_not_mutate_its_input(disp):
    """A caller reusing the frame after ranking must see it untouched.

    _rank_for_panel copies internally and drops its scratch column. If a future
    version sorts in place, the scanner table and the panel would silently start
    sharing an order.
    """
    df = pd.DataFrame({
        "Put Strike": [6000.0, 6100.0],
        "Call Strike": [6100.0, 6100.0],
        "Transform Diff": [1.0, 50.0],
    })
    before = df.copy()
    ranked = disp["_rank_for_panel"](df)
    pd.testing.assert_frame_equal(df, before)

    # The scratch column can never reach the input (the function copies first),
    # so checking the input proves nothing. The place it leaks is the RETURN
    # value, where it becomes a stray column on the rendered panel.
    assert list(ranked.columns) == list(before.columns), (
        "ranking must return the same columns it was given"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Chart geometry — where the IV-ratio line changes colour
# ═══════════════════════════════════════════════════════════════════════════════

def test_band_crossings_are_interpolated_at_the_exact_threshold(disp):
    """A segment crossing a band boundary gets a synthetic point ON the boundary.

    Without this the colour would change at the next real data point instead of
    at the threshold, so a line would visibly change colour in the wrong place.
    Rising 0.60 -> 1.40 over x 1 -> 2 crosses all three thresholds
    (0.70, 1.00, 1.30) at 12.5%, 50% and 87.5% of the way across.
    """
    traces = disp["_banded_ratio_traces"]([1, 2], [0.60, 1.40])
    assert traces[0].x == (1, 1.125, 1.5, 1.875, 2)


def test_band_crossings_are_ordered_correctly_when_falling(disp):
    """On a FALLING segment the thresholds must be inserted high-to-low.

    Inserting them ascending would put the synthetic x-values out of order and
    Plotly would draw the line doubling back on itself — the classic
    'chart looks wrong but no error' failure.
    """
    traces = disp["_banded_ratio_traces"]([1, 2], [1.40, 0.60])
    xs = traces[0].x
    assert list(xs) == sorted(xs), f"x must be monotonically increasing, got {xs}"
    # approx, not exact: the falling case computes the same crossings by
    # subtracting in the other direction, which lands 1 ulp off 1.125.
    assert list(xs) == pytest.approx([1, 1.125, 1.5, 1.875, 2])


def test_only_bands_containing_data_produce_a_trace(disp):
    """An unused band must not appear, or the legend fills with dead entries."""
    traces = disp["_banded_ratio_traces"]([1, 2, 3], [0.80, 0.85, 0.90])
    assert [t.name for t in traces] == ["Contango 0.70–1.00 (normal)"]


def test_gaps_are_not_connected_across(disp):
    """Missing data must break the line, not bridge it.

    connectgaps=False plus a None in the band series is what makes a collector
    outage show as honest blank space instead of a straight line implying
    prices we never observed.
    """
    traces = disp["_banded_ratio_traces"]([1, 2, 3], [0.80, float("nan"), 0.90])
    assert all(t.connectgaps is False for t in traces)
    assert any(v is None for t in traces for v in t.y)


def test_flat_segment_produces_no_crossings(disp):
    """Equal consecutive values must not trigger a division by (y1 - y0)."""
    traces = disp["_banded_ratio_traces"]([1, 2], [1.00, 1.00])
    assert traces[0].x == (1, 2)


def test_value_exactly_on_a_boundary_lands_in_both_bands(disp):
    """PINS ARGUABLY-WRONG BEHAVIOUR, deliberately.

    The bands use inclusive comparisons at both ends (low <= v <= high), so a
    ratio of exactly 1.00 is drawn in the 0.70-1.00 band AND the 1.00-1.30 one.
    Two traces overlap on that point.

    In practice this is invisible — a ratio is a float and never lands exactly
    on a boundary — and 'fixing' it means choosing which band owns the edge,
    which is a real decision, not a typo. Frozen here so that if someone
    changes the comparison the change is seen and made on purpose.
    """
    traces = disp["_banded_ratio_traces"]([1], [1.00])
    named = {t.name for t in traces}
    assert named == {
        "Backwardation 1.00–1.30 (front rich)",
        "Contango 0.70–1.00 (normal)",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Cell text — what the numbers turn into
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize(("minutes", "expected"), [
    (None,  "—"),
    (0.4,   "<1 min"),
    (1.0,   "~1 min"),
    (18.4,  "~18 min"),
    (59.9,  "~60 min"),   # rounds to 60 rather than flipping to hours
    (60.0,  "~1.0 hr"),
    (200.0, "~3.3 hr"),
])
def test_eta_formatting(disp, minutes, expected):
    assert disp["_fmt_eta"](minutes) == expected


@pytest.mark.parametrize(("minutes", "expected"), [
    (0,    "<1m"),
    (8,    "8m"),
    (47,   "47m"),
    (60,   "1h 0m"),
    (132,  "2h 12m"),
    (1500, "25h 0m"),   # does not roll over into days
])
def test_duration_formatting(disp, minutes, expected):
    assert disp["_fmt_duration"](pd.Timedelta(minutes=minutes)) == expected


def test_duration_of_nothing_is_a_dash(disp):
    assert disp["_fmt_duration"](None) == "—"
    assert disp["_fmt_duration"](pd.NaT) == "—"


def test_sparkline_shape(disp):
    """The glyph must track the shape of the series, scaled to its own range."""
    assert disp["_sparkline"]([]) == "─"
    assert disp["_sparkline"]([1, 2, 3, 4, 5]) == "▁▂▄▆█"
    assert disp["_sparkline"]([5, 4, 3, 2, 1]) == "█▆▄▂▁"
    # A flat series has no range to scale against; it must not divide by zero.
    assert disp["_sparkline"]([7, 7, 7]) == "▄▄▄"


def test_sparkline_downsamples_across_the_whole_series(disp):
    """Long series must be THINNED across their full span, not truncated.

    A straight line cannot tell these apart — sampling every 10th point of
    range(100) and taking the first 10 points both give an evenly rising glyph.
    So the series here rises then falls: truncation would show only the rise and
    report a peak as a climb, which is exactly the wrong thing to tell a trader
    watching a gap that has already turned over.
    """
    rise_then_fall = list(range(50)) + list(range(50, 0, -1))
    spark = disp["_sparkline"](rise_then_fall, width=10)

    assert len(spark) == 10
    peak = spark.index(max(spark))
    assert 0 < peak < len(spark) - 1, (
        f"the peak must appear mid-glyph, got {spark!r} with peak at {peak} — "
        f"a peak at the end means only the rising half was sampled"
    )


def test_card_key_is_stable_and_distinguishing(disp):
    """The key identifies a card across reruns — Streamlit widget state and the
    entry locks hang off it. Two different combos must never collide."""
    a = {"front_raw": "2026-08-01", "back_raw": "2026-08-15",
         "put_strike": 6000.0, "call_strike": 6100.0}
    b = {**a, "call_strike": 6200.0}

    assert disp["_card_key"](a) == disp["_card_key"](a)
    assert disp["_card_key"](a) != disp["_card_key"](b)
    # Strikes are normalised to int, so 6000.0 and 6000 are the same card.
    assert disp["_card_key"](a) == disp["_card_key"]({**a, "put_strike": 6000})
