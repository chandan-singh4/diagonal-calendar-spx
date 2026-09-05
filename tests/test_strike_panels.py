"""The strike stack: what is drawn, and in what order.

Volume and open interest share one strike axis so they can be read against
each other vertically. Which panel is which is exactly the kind of thing a
later edit reorders by accident, so it is pinned rather than left to the eye.
"""
import pandas as pd
import pytest

from views import gex as view

STRIKES = [7700.0, 7750.0, 7800.0]


def _shown() -> pd.DataFrame:
    return pd.DataFrame({
        "strike": STRIKES,
        "call_gex": [1.0, 2.0, 3.0], "put_gex": [1.0, 1.0, 1.0],
        "net_gex": [0.0, 1.0, 2.0], "abs_gex": [2.0, 3.0, 4.0],
        "call_oi": [10.0, 20.0, 30.0], "put_oi": [5.0, 5.0, 5.0],
        "call_volume": [100.0, 200.0, 300.0],
        "put_volume": [50.0, 60.0, 70.0],
    })


def _titles(fig) -> list[str]:
    """Subplot headings only — the spot-price tag is an annotation too."""
    return [a.text for a in fig.layout.annotations
            if a.text and not a.text[0].isdigit()]


def _figure(shown, view_name="Net Gamma", stack=False):
    # __wrapped__ steps past @st.cache_data: this asks what the function
    # DRAWS, and a cache hit would answer a different question.
    return view._strike_figure.__wrapped__(
        shown, shown, 7750.0, view_name, None, stack, 1)


def test_volume_is_drawn_above_open_interest():
    """Volume on top of open interest, not under it.

    They were the other way round. Volume is the only one of the two that
    moves during the day, and the reader's question is "did today's trading
    stick?" — which reads downward, from the live number to the settled one.
    """
    shown = _shown()
    fig = _figure(shown)
    assert _titles(fig) == ["Gamma Exposure", "Volume", "Open Interest"]

    # And the BARS agree with those headings. Checking the titles alone would
    # pass with the two panels' data swapped underneath them, which is worse
    # than the original order: the chart would then be confidently mislabelled.
    # (A mutation that moved the volume bars a row down and left the titles
    # alone slipped through until this was added.)
    on = lambda axis: [list(t.y) for t in fig.data if t.yaxis == axis]
    assert list(shown["call_volume"]) in on("y3")
    assert list(shown["call_oi"]) in on("y4")
    assert list(shown["call_volume"]) not in on("y4")


def test_the_headings_carry_no_parenthetical():
    """Plain nouns, and the timing lives in the caption instead.

    The headings briefly read "Volume (today, still moving)" and "Open Interest
    (as of last night's close)". Both were true, and both were removed: a
    parenthetical on every title is read once and is furniture on every render
    after. The distinction still has to be stated SOMEWHERE, though — losing it
    entirely is what made these two panels confusable in the first place — so
    this asserts the headings are bare, not that the explanation is gone.
    """
    assert _titles(_figure(_shown())) == ["Gamma Exposure", "Volume",
                                          "Open Interest"]


def test_there_is_no_change_in_open_interest_panel():
    """Three panels, not four. The overnight change had a row of its own for
    one commit; the numbers are still in the positioning table below, which is
    where they get a verdict attached rather than only a bar."""
    fig = _figure(_shown())
    assert len(_titles(fig)) == 3
    assert not any("Change" in t for t in _titles(fig))
    assert {t.yaxis for t in fig.data} == {"y", "y2", "y3", "y4"}


def test_the_mirror_toggle_flips_puts_in_both_lower_panels():
    """What Stacked/Mirrored is FOR: mirrored puts hang below the axis so the
    two sides can be compared at a glance, stacked they sit on top of the
    calls. Both lower panels obey it — one obeying and one not would be read
    as a difference in the data."""
    shown = _shown()
    mirrored, stacked = _figure(shown, stack=False), _figure(shown, stack=True)
    below = lambda f, ax: [list(t.y) for t in f.data if t.yaxis == ax]
    assert [-v for v in shown["put_volume"]] in below(mirrored, "y3")
    assert [-v for v in shown["put_oi"]] in below(mirrored, "y4")
    assert list(shown["put_volume"]) in below(stacked, "y3")
    assert list(shown["put_oi"]) in below(stacked, "y4")


@pytest.mark.parametrize("view_name", ["Call vs Put", "Abs Gamma",
                                       "Net Gamma", "Delta Exposure"])
def test_every_gamma_view_still_draws_three_panels(view_name):
    """The gamma view changes only the top panel. Delta Exposure takes a
    different code path to build it, which is why it is worth asking whether
    the two panels underneath survive the trip."""
    fig = _figure(_shown(), view_name=view_name)
    assert _titles(fig) == ["Gamma Exposure", "Volume", "Open Interest"]
