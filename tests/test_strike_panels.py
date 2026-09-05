"""The strike stack: what is drawn, in what order, and keyed on what.

Volume, open interest and the overnight change in open interest share one
strike axis so they can be read against each other vertically. That is the
whole point of the panel, and it is exactly the kind of thing a later edit
reorders by accident, so the order is pinned rather than left to the eye.
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


def _prior(call_oi=(8.0, 25.0, 30.0), put_oi=(5.0, 4.0, 5.0)) -> pd.DataFrame:
    return pd.DataFrame({"strike": STRIKES,
                         "call_oi": list(call_oi), "put_oi": list(put_oi)})


def _titles(fig) -> list[str]:
    """Subplot headings only — the spot-price tag is an annotation too."""
    return [a.text for a in fig.layout.annotations
            if a.text and not a.text[0].isdigit()]


def _figure(shown, change, fingerprint=1, view_name="Net Gamma", stack=False):
    # __wrapped__ steps past @st.cache_data: this asks what the function
    # DRAWS, and a cache hit would answer a different question.
    return view._strike_figure.__wrapped__(
        shown, shown, 7750.0, view_name, None, stack, 1, fingerprint,
        _change=change)


def test_volume_is_drawn_above_open_interest():
    """Volume on top of open interest, not under it.

    They were the other way round. Volume is the only one of the two that
    moves during the day, and the reader's question is "did today's trading
    stick?" — which reads downward, from the live number to the settled one.
    """
    shown = _shown()
    fig = _figure(shown, view._oi_change_frame(shown, _prior()))
    assert _titles(fig) == [
        "Gamma Exposure",
        "Volume (today, still moving)",
        "Open Interest (as of last night's close)",
        "Change in Open Interest (overnight)",
    ]

    # And the BARS agree with those headings. Checking the titles alone would
    # pass with the two panels' data swapped underneath them, which is worse
    # than the original order: the chart would then be confidently mislabelled.
    # (A mutation that moved the volume bars to row 3 and left the titles
    # alone slipped through until this was added.)
    on = lambda axis: [list(t.y) for t in fig.data if t.yaxis == axis]
    assert list(shown["call_volume"]) in on("y3")
    assert list(shown["call_oi"]) in on("y4")
    assert list(shown["call_volume"]) not in on("y4")


def test_the_change_panel_is_the_bottom_row_of_the_same_figure():
    """One figure, not two cards. Sharing the x axis is the feature: the
    delta panel used to be a separate chart further down the page with its
    own strike axis, and lining a bar up against the one above it meant
    scrolling and remembering."""
    fig = _figure(_shown(), view._oi_change_frame(_shown(), _prior()))
    rows = {t.yaxis for t in fig.data}
    assert len(rows) == 5          # y, y2 (the volume overlay), y3, y4, y5
    assert fig.layout.xaxis4.anchor is not None


def test_no_previous_session_leaves_three_panels_not_a_blank_one():
    """An absent comparison is not a zero one. With nothing to subtract the
    panel is not drawn at all and the caller says why — an empty axis would
    read as "nothing changed overnight", which is a different claim."""
    assert view._oi_change_frame(_shown(), None) is None
    assert view._oi_change_frame(_shown(), pd.DataFrame()) is None
    fig = _figure(_shown(), None)
    assert len(_titles(fig)) == 3
    assert "Change in Open Interest" not in " ".join(_titles(fig))


def test_a_count_that_did_not_move_is_not_the_same_as_no_count():
    """The other half of that distinction: an unchanged count is a real
    reading and returns an EMPTY frame, not None. The figure still drops the
    panel — there is nothing to draw — but the caller must not print the
    "no previous session" notice, because there was one."""
    unchanged = view._oi_change_frame(
        _shown(), _prior(call_oi=(10.0, 20.0, 30.0), put_oi=(5.0, 5.0, 5.0)))
    assert unchanged is not None
    assert unchanged.empty


def test_the_delta_panel_keeps_puts_downward_even_when_stacked():
    """The mirror toggle must not reach the bottom panel. Up and down there
    already mean opened and closed; letting the toggle flip the put side
    would put two meanings on one axis and the reader could not tell a
    closed put from a mirrored one."""
    change = view._oi_change_frame(_shown(), _prior())
    stacked = _figure(_shown(), change, stack=True)
    mirrored = _figure(_shown(), change, stack=False)
    bottom = lambda f: [list(t.y) for t in f.data if t.yaxis == "y5"]
    assert bottom(stacked) == bottom(mirrored)


def test_the_prior_session_is_part_of_the_cache_key():
    """`_change` is passed unhashed, so it contributes NOTHING to the key.
    Without the fingerprint the figure would be keyed on today's snapshot
    alone and would keep serving the pre-overnight delta panel after the new
    count landed — the same class of bug as the expiry that did not move."""
    assert view._prior_fingerprint(_prior()) != view._prior_fingerprint(
        _prior(call_oi=(8.0, 25.0, 31.0)))
    assert view._prior_fingerprint(None) == 0
    assert view._prior_fingerprint(pd.DataFrame()) == 0

    params = view._strike_figure.__wrapped__.__code__.co_varnames[
        :view._strike_figure.__wrapped__.__code__.co_argcount]
    keyed = [p for p in params if not p.startswith("_")]
    assert "prior_fingerprint" in keyed


@pytest.mark.parametrize("view_name", ["Call vs Put", "Abs Gamma",
                                       "Net Gamma", "Delta Exposure"])
def test_every_gamma_view_still_draws_four_panels(view_name):
    """The gamma view changes only the top panel. Delta Exposure takes a
    different code path to build it, which is why it is worth asking whether
    the three panels underneath survive the trip."""
    shown = _shown()
    if view_name == "Delta Exposure":
        pytest.importorskip("core.gex")
    fig = _figure(shown, view._oi_change_frame(shown, _prior()),
                  view_name=view_name)
    assert len(_titles(fig)) == 4
