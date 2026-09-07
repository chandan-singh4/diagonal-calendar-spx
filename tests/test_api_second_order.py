"""Vanna and charm, served — closing the gap the M6 plan called tab 2's.

WHAT THIS PINS AND WHY IT IS SPLIT IN TWO.

The endpoint had to do two separable things and they fail in different ways,
so they are checked apart:

  * THE ARITHMETIC — `computed.second_order_exposure` must return exactly what
    `core/gex.py` returns for the same inputs. Nothing here re-derives a
    Greek; `tests/test_second_order_greeks.py` does that against a numerical
    derivative. What is checked here is that the server did not quietly grow a
    second scaling, a second sign convention, or a different day.

  * THE WIRING — `measure=` has to reach the right function, reject a name it
    does not know, and leave `measure=gamma` byte-identical to what the route
    returned before this parameter existed.

THE FIXTURE CHAIN IS BUILT HERE RATHER THAN BORROWED. `conftest`'s
`_mc_legs` writes `iv=0.184`, which under iv_engine's percentage convention
means 0.184% volatility — realistic-looking and three orders of magnitude off.
Vanna computed from it rounds to nothing, so a comparison against it would
pass while comparing zero to zero. Every value assertion below therefore also
asserts the number is NOT zero, which is the only thing that makes the rest of
it mean anything.
"""
from __future__ import annotations

import pandas as pd
import pytest
from conftest import make_transform_history
from fastapi.testclient import TestClient

import config
from api import computed
from api.app import create_app
from core import gex

# 20:01 UTC is 16:01 in New York — one minute after the close, so no day is
# left. Used where the remainder must be a known quantity rather than whatever
# the wall clock happens to be.
AFTER_CLOSE = "2026-09-04 20:01:00"
MIDDAY = "2026-09-04 14:00:00"  # 10:00 in New York: six hours to the close


@pytest.fixture
def chain() -> pd.DataFrame:
    """A small two-expiry chain with volatility in the units iv_engine wants.

    18.5 means 18.5%. Strikes straddle the spot so both signs of `d2` appear —
    a chain entirely above or entirely below the money would let a sign error
    through, because every row would be wrong in the same direction and the
    totals would still look plausible.
    """
    rows = []
    for expiry, dte in (("2026-09-11", 7), ("2026-09-18", 14)):
        for strike in (5900.0, 6000.0, 6100.0):
            for right in ("C", "P"):
                rows.append(dict(expiry=expiry, dte=dte, strike=strike,
                                 right=right, iv=18.5, open_interest=1000))
    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────────────────────
# The arithmetic is core/'s, not the server's
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("measure", ["vanna", "charm"])
def test_the_server_returns_exactly_what_core_returns(chain, measure):
    """ONE DEFINITION, TWO CALLERS — the same thing DEBT-041 was opened for.

    The page calls `core.gex.vanna_by_strike` directly; the server calls it
    through `second_order_exposure`. If that wrapper ever grows a rule of its
    own — a different day fraction, a rate read from somewhere else, a rescale
    — the two screens disagree and this is the test that says so.
    """
    served = computed.second_order_exposure(
        chain, 6000.0, measure, AFTER_CLOSE,
        r=config.RISK_FREE_RATE, q=config.DIVIDEND_YIELD,
        display_tz=config.DISPLAY_TIMEZONE)

    builder = gex.vanna_by_strike if measure == "vanna" else gex.charm_by_strike
    direct = builder(chain, 6000.0,
                     r=config.RISK_FREE_RATE, q=config.DIVIDEND_YIELD,
                     expiry=None,
                     day_remainder=gex.day_remainder(AFTER_CLOSE,
                                                     config.DISPLAY_TIMEZONE))

    pd.testing.assert_frame_equal(served["by_strike"], direct)
    # Without this the assertion above would be satisfied by two empty frames.
    assert not direct.empty
    assert direct.iloc[:, 1:].abs().to_numpy().sum() > 0, (
        "a frame of zeros would make every comparison here vacuous"
    )


def test_the_assumptions_are_returned_not_merely_used(chain):
    """r AND q ARE NOT IN THE RECORD. They are stated constants, and a reader
    comparing this against a vendor's vanna cannot interpret the difference
    without knowing which ones were used. A number whose assumptions are
    invisible is not a smaller answer, it is a different kind of thing."""
    out = computed.second_order_exposure(
        chain, 6000.0, "charm", MIDDAY,
        r=0.05, q=0.02, display_tz=config.DISPLAY_TIMEZONE)

    assert out["assumptions"] == {
        "risk_free_rate": 0.05,
        "dividend_yield": 0.02,
        "day_remainder": 0.25,
        "snapshot_timestamp": MIDDAY,
    }


def test_gamma_is_refused_by_the_second_order_path(chain):
    """The guard exists because the two paths return DIFFERENTLY SHAPED
    frames — `call_gex` against `call_vex`. Falling through to one of them
    with the other's name would produce a chart that draws cleanly under the
    wrong title."""
    with pytest.raises(ValueError, match="expected 'vanna' or 'charm'"):
        computed.second_order_exposure(
            chain, 6000.0, "gamma", AFTER_CLOSE,
            r=0.04, q=0.012, display_tz=config.DISPLAY_TIMEZONE)


# ─────────────────────────────────────────────────────────────────────────────
# day_remainder — moved out of views/gex.py, and never tested until now
# ─────────────────────────────────────────────────────────────────────────────

def test_the_day_remainder_is_the_time_left_before_the_close():
    """10:00 in New York leaves six hours of a 24-hour day."""
    assert gex.day_remainder(MIDDAY, "America/New_York") == pytest.approx(0.25)


def test_a_snapshot_after_the_close_has_no_day_left():
    """Clamped at zero rather than going negative. A negative remainder would
    subtract from `dte` and hand charm a shorter time than the contract has,
    which is worst exactly where charm matters most — the last day."""
    assert gex.day_remainder(AFTER_CLOSE, "America/New_York") == 0.0


def test_an_unreadable_timestamp_falls_back_to_whole_days():
    """Zero, which is the same answer as "the close has passed" — the
    conservative one. It makes charm fall back on whole days rather than
    inventing a fraction out of a value nobody could read."""
    assert gex.day_remainder("not a timestamp", "America/New_York") == 0.0
    assert gex.day_remainder(None, "America/New_York") == 0.0


def test_the_timezone_is_the_callers_to_supply():
    """core/ may not read config (tests/test_layering.py), so the zone arrives
    as an argument — and it genuinely changes the answer, which is what makes
    passing it a real decision rather than ceremony. 14:00 UTC is 10:00 in New
    York but 15:00 in London, so London has one hour left, not six."""
    assert gex.day_remainder(MIDDAY, "Europe/London") == pytest.approx(1 / 24)


# ─────────────────────────────────────────────────────────────────────────────
# The route
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def client(tmp_path, temp_db) -> TestClient:
    make_transform_history(temp_db, [6.0, 6.5], interval_minutes=5)
    return TestClient(create_app(db_path=temp_db, state_dir=str(tmp_path)))


@pytest.mark.parametrize("measure", ["gamma", "vgex", "delta", "vanna", "charm"])
def test_every_measure_answers_and_says_which_one_it_is(client, measure):
    """`measure` is echoed for the same reason `expiry` is: a gamma figure, a
    vanna figure and a charm figure are different numbers of similar size and
    look identical on a screen."""
    body = client.get(f"/mission/gamma?measure={measure}").json()

    assert body["measure"] == measure
    assert "rows" in body


def test_the_default_is_still_gamma(client):
    """LOAD-BEARING. Anything already reading this endpoint predates the
    parameter and must not have to learn it exists."""
    default = client.get("/mission/gamma").json()
    explicit = client.get("/mission/gamma?measure=gamma").json()

    assert default == explicit
    assert default["measure"] == "gamma"
    # The columns the four GEX/DEX views draw, unchanged.
    assert "flip_strike" in default


def test_an_unknown_measure_is_refused_rather_than_defaulted(client):
    """422, not a quiet fall back to gamma. A client that asked for charm and
    was handed gamma would draw the wrong chart under the right title — the
    exact failure echoing `measure` is meant to prevent.

    THIS TEST USED "delta" AS ITS UNKNOWN NAME and caught its own obsolescence
    when delta became a real measure a day later: it started failing with 200
    instead of 422. That is the test working. "theta" is not a view on this
    tab and there is no column to build one from, so it is a safer stand-in —
    though the honest note is that any name can stop being unknown, and the
    guard being tested is the list, not this word.
    """
    response = client.get("/mission/gamma?measure=theta")

    assert response.status_code == 422
    assert "gamma, vgex, delta, vanna, charm" in response.json()["detail"]


@pytest.mark.parametrize("measure", ["gamma", "delta", "vanna", "charm"])
def test_each_measure_survives_being_asked_twice(client, measure):
    """THE CACHE HANDS BACK THE SAME OBJECT. This route already had that bug
    once — it lifted its frame out with `pop`, which emptied the cached entry,
    so the first request worked and the second raised KeyError. The
    second-order branch caches too, so it gets the same guard.

    The assertion is on the SECOND answer, and on its equality with the first.
    """
    first = client.get(f"/mission/gamma?measure={measure}")
    second = client.get(f"/mission/gamma?measure={measure}")

    assert second.status_code == 200, second.text
    assert second.json() == first.json()


def test_delta_returns_no_assumptions_block(client):
    """ITS ABSENCE IS THE SIGNAL. Delta exposure weights the `delta` the
    broker sent, so no rate, no yield and no fraction of a day stands behind
    it. An empty assumptions block would say the question was considered and
    the answer was none — a different claim from "not applicable"."""
    delta = client.get("/mission/gamma?measure=delta").json()
    charm = client.get("/mission/gamma?measure=charm").json()

    assert "assumptions" not in delta
    assert "assumptions" in charm, "the contrast is the point"
    assert delta["measure"] == "delta"


def test_the_measures_do_not_share_a_cache_entry(client):
    """Keyed on the measure as well as the snapshot. Without that, whichever
    was asked for first would be served for all three — and it would look
    right, because every one of them is a plausible column of large numbers
    against the same strikes."""
    vanna = client.get("/mission/gamma?measure=vanna").json()
    charm = client.get("/mission/gamma?measure=charm").json()

    assert list(vanna["rows"][0]) != list(charm["rows"][0]), (
        "vanna returns call_vex/put_vex, charm returns call_cex/put_cex"
    )


# ─────────────────────────────────────────────────────────────────────────────
# The expiry selector's options
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("measure", ["gamma", "delta", "vanna", "charm"])
def test_every_measure_carries_the_expiry_options(client, measure):
    """The control is on screen whichever view is selected, so the list it
    needs cannot ride on one of them."""
    body = client.get(f"/mission/gamma?measure={measure}").json()

    assert body["expiries"], "a snapshot with a chain has expiries"
    # A SUBSET, not an equality, since 2026-09-06: the expiry picker needs
    # each option's gamma, weekday and filter membership as well, and this
    # test is here to say the ORIGINAL THREE never quietly stop being served.
    # A stricter check would fail every time the picker grows a column, which
    # teaches whoever hits it to loosen the assertion rather than read it.
    assert {"key", "label", "dte"} <= set(body["expiries"][0])


def test_the_expiry_keys_are_the_ones_the_endpoint_accepts(client):
    """A LIST OF OPTIONS THAT DOES NOT WORK AS INPUT IS WORSE THAN NO LIST.
    `expiry` is a display key, and the whole reason these are served is that a
    caller could not otherwise discover them — so each one is fed straight
    back in and has to be accepted."""
    body = client.get("/mission/gamma").json()

    for option in body["expiries"]:
        scoped = client.get("/mission/gamma", params={"expiry": option["key"]})
        assert scoped.status_code == 200, option
        # A LIST OF ONE since `expiry` became repeatable — FastAPI collects
        # the parameter whether it appears once or five times, so the echo is
        # always a list and a caller comparing it to a bare string would find
        # them unequal while everything else was correct.
        assert scoped.json()["expiry"] == [option["key"]]


def test_the_third_friday_keeps_both_of_its_contracts():
    """SPX lists two contracts for the third Friday — one settling on the open
    and one on the close — and they are different instruments with different
    exposure. Collapsing them to one date is BUG-023, still open; the options
    list must not be where that happens.

    Built here rather than off the fixture db, which has no third Friday.
    """
    chain = pd.DataFrame([
        dict(expiry="2026-09-18 (AM)", dte=14, strike=6000.0, right="C"),
        dict(expiry="2026-09-18", dte=14, strike=6000.0, right="C"),
        dict(expiry="2026-09-25", dte=21, strike=6000.0, right="C"),
    ])

    options = computed.expiry_options(chain)

    assert [o["key"] for o in options] == [
        "2026-09-18 (AM)", "2026-09-18", "2026-09-25"], (
        "core.contract.sort_key puts the a.m. contract first; plain string "
        "sorting would not"
    )
    assert "AM settled" in options[0]["label"]
    assert "AM settled" not in options[1]["label"]


def test_no_chain_means_no_options_rather_than_a_crash():
    """An empty snapshot is a normal gap in this record, not a fault."""
    assert computed.expiry_options(pd.DataFrame()) == []
    assert computed.expiry_options(None) == []


# ─────────────────────────────────────────────────────────────────────────────
# The peak-strike label
# ─────────────────────────────────────────────────────────────────────────────

def test_the_peak_strike_label_names_the_side_that_owns_it():
    """"7,720" alone does not say whose peak it is.

    The Streamlit strip has always read "7,720 (Put)", and the served label
    must say the same thing -- a strike shown on one screen with a side and on
    the other without it is two different readings of one snapshot.

    Pinned on `core.format.peak_label` AND on the served labels, because the
    join used to be written out by hand in three places and this test exists
    to stop the fourth.
    """
    from api import computed
    from core.format import peak_label

    summary = {"peak_strike": 7720.0, "peak_side": "Put"}
    assert peak_label(summary) == "7,720 (Put)"
    assert computed.exposure_labels(summary)["peak_strike"] == "7,720 (Put)"


def test_a_missing_peak_is_blank_rather_than_a_side_with_no_strike():
    """No peak is a real state on a thin chain, not an error."""
    from core.format import peak_label

    assert peak_label({"peak_strike": None, "peak_side": None}) == "N/A"


# ─────────────────────────────────────────────────────────────────────────────
# The pair controls
# ─────────────────────────────────────────────────────────────────────────────

def _controls_chain():
    """Three expiries, two sides, deliberately UNEQUAL strike coverage."""
    import pandas as pd

    rows = []
    for expiry, dte, strikes in (
        ("2026-09-04", 0, [7600.0, 7700.0, 7800.0]),
        ("2026-09-11", 7, [7600.0, 7700.0, 7800.0, 7900.0]),
        ("2026-09-18", 14, [7700.0, 7800.0]),
    ):
        for side in ("CALL", "PUT"):
            for strike in strikes:
                rows.append(dict(expiry=expiry, dte=dte, side=side, strike=strike))
    return pd.DataFrame(rows)


def test_a_back_expiry_is_never_earlier_than_the_front():
    """A diagonal whose back leg expires first is not a diagonal (Chandan,
    2026-08-19). Before the narrowing, twenty wrong answers sat one click
    away and the page only complained after the fact."""
    from api import computed

    body = computed.pair_controls(_controls_chain(), 7700.0, front="2026-09-11")
    assert [e["key"] for e in body["back_expiries"]] == ["2026-09-18"]


def test_the_strikes_offered_are_in_BOTH_legs():
    """A strike listed by only one expiry cannot be a diagonal at all, and
    offering it draws an empty chart with nothing saying why. 7900 is in the
    front here and not the back; 7600 is in the back and not the front."""
    from api import computed

    body = computed.pair_controls(_controls_chain(), 7700.0,
                                  front="2026-09-11", back="2026-09-18")
    assert body["call_strikes"] == [7700.0, 7800.0]
    assert body["put_strikes"] == [7700.0, 7800.0]


def test_the_defaults_are_the_shape_of_the_trade():
    """Call nearest spot, put a hundred points below it."""
    from api import computed

    body = computed.pair_controls(_controls_chain(), 7800.0,
                                  front="2026-09-04", back="2026-09-11")
    assert body["call_strike"] == 7800.0
    assert body["put_strike"] == 7700.0


def test_the_furthest_expiry_as_front_offers_no_pair_rather_than_a_false_one():
    """Reachable by choosing the last expiry collected. There is genuinely no
    back leg, so `back` is None and the strike lists are empty -- naming a
    pair that is not one would be worse than offering nothing."""
    from api import computed

    body = computed.pair_controls(_controls_chain(), 7700.0, front="2026-09-18")
    assert body["back"] is None
    assert body["call_strikes"] == [] and body["put_strikes"] == []


# ─────────────────────────────────────────────────────────────────────────────
# The Strike Detail figures
# ─────────────────────────────────────────────────────────────────────────────

def test_one_atm_record_gives_an_unknown_change_not_a_flat_one():
    """A single record on file means the move is UNKNOWN. Returning 0.0 would
    draw a green arrow reading "unchanged", which is a claim about the market
    made from the absence of data."""
    from api import computed

    out = computed.atm_headline([{"atm_avg_iv": 0.1850}])
    assert out["atm_iv"] == pytest.approx(18.50)
    assert out["change"] is None


def test_no_atm_record_is_blank_on_both_figures():
    from api import computed

    assert computed.atm_headline([]) == {"atm_iv": None, "change": None}


def test_the_atm_change_is_against_the_previous_record():
    from api import computed

    out = computed.atm_headline([{"atm_avg_iv": 0.1900}, {"atm_avg_iv": 0.1850}])
    assert out["change"] == pytest.approx(0.50)


def test_iv_is_served_as_a_percent_like_every_other_iv_here():
    """Stored as a fraction, served as a percent. Half this repo's IV bugs are
    this boundary, so it is asserted rather than assumed."""
    from api import computed

    assert computed.atm_headline([{"atm_avg_iv": 0.2}])["atm_iv"] == pytest.approx(20.0)


def test_a_leg_with_no_iv_on_one_side_has_no_ratio():
    """A ratio needs both halves. Treating a missing IV as zero would make the
    ratio either zero or infinite, and both would be drawn as if measured."""
    import pandas as pd

    from api import computed

    chain = pd.DataFrame([
        dict(expiry="2026-09-04", side="CALL", strike=7700.0, iv=None,
             bid=1.0, ask=2.0, mark=1.5, dte=0),
        dict(expiry="2026-09-11", side="CALL", strike=7700.0, iv=0.20,
             bid=3.0, ask=4.0, mark=3.5, dte=7),
        dict(expiry="2026-09-04", side="PUT", strike=7700.0, iv=0.18,
             bid=1.0, ask=2.0, mark=1.5, dte=0),
        dict(expiry="2026-09-11", side="PUT", strike=7700.0, iv=0.20,
             bid=3.0, ask=4.0, mark=3.5, dte=7),
    ])
    legs = computed.strike_legs(chain, "2026-09-04", "2026-09-11", 7700.0, 7700.0)
    call = next(leg for leg in legs if leg["label"] == "Call")
    assert call["iv_ratio"] is None


# ─────────────────────────────────────────────────────────────────────────────
# The Calendar Edge headline
# ─────────────────────────────────────────────────────────────────────────────

def test_the_iv_index_gives_each_expiry_one_vote():
    """A MEAN OF MEANS, not a mean. Two expiries, one quoted eight times and
    one twice: a flat average would land near the heavily-quoted one, and the
    figure would move when the chain's shape moved rather than when
    volatility did."""
    import iv_engine

    chain = pd.DataFrame(
        [{"expiry": "A", "iv": 10.0}] * 8 + [{"expiry": "B", "iv": 20.0}] * 2
    )
    assert iv_engine.iv_index(chain) == pytest.approx(15.0)
    assert chain["iv"].mean() == pytest.approx(12.0)  # what a flat mean gives


def test_an_expiry_with_no_atm_iv_is_blank_not_zero():
    """`atm_iv` raises on a chain with no IV at the nearest strike -- a real
    state near the close. The box says N/A rather than 0.00%."""
    from api import computed

    chain = pd.DataFrame([
        dict(expiry="2026-09-04", side="CALL", strike=7700.0, iv=None),
        dict(expiry="2026-09-11", side="CALL", strike=7700.0, iv=20.0),
    ])
    out = computed.edge_headline(chain, 7700.0, "2026-09-04", "2026-09-11")
    assert out["front_iv"] is None
    assert out["back_iv"] == pytest.approx(20.0)
    assert out["ratio"] is None      # a ratio needs both halves
    assert out["iv_index"] == pytest.approx(20.0)


# ─────────────────────────────────────────────────────────────────────────────
# The header strip, and the drill-down's addresses
# ─────────────────────────────────────────────────────────────────────────────

def test_the_sweep_carries_keys_alongside_its_labels():
    """A LABEL IS NOT AN ADDRESS. "2026-09-23 (19d)" is what the table shows;
    "2026-09-23" is what an endpoint taking an expiry wants, and passing the
    label gets a 500. Served so no client has to know that rule."""
    from api import computed

    sweep = pd.DataFrame([
        {"Front Expiry": "2026-09-23 (19d)", "Back Expiry": "2026-09-29 (25d)"},
    ])
    out = computed.add_raw_expiries(sweep)
    assert out["Front Raw"].iloc[0] == "2026-09-23"
    assert out["Back Raw"].iloc[0] == "2026-09-29"
    # The cached sweep must not grow columns under every later reader.
    assert "Front Raw" not in sweep.columns


def test_the_header_never_invents_a_flat_day():
    """Change is measured from the PRIOR session's close, and the label says
    so. A client subtracting from whatever was last on screen would produce a
    number that changes meaning at midnight and on the first day of
    collection."""
    from datetime import UTC, datetime

    from api import computed

    chain = pd.DataFrame([
        dict(expiry="2026-09-04", side="CALL", strike=7700.0, iv=20.0,
             gamma=0.001, open_interest=100, dte=0),
    ])
    out = computed.header(
        spx_price=7718.36, vix_value=14.54, prev_close=7747.59,
        session_open=7700.0, chain_df=chain, snap_age_secs=42,
        now_et=datetime(2026, 9, 4, 12, 0, tzinfo=UTC),
    )
    assert out["change"]["points"] == pytest.approx(7718.36 - 7747.59)
    assert out["change"]["reference_label"] == "Prev Close 7,748"
    assert out["age_seconds"] == 42
    assert out["dot"] == "green"


def test_the_header_serves_the_thresholds_rather_than_the_session_name():
    """The client counts upward and recolours as it passes them. Which
    numbers those are depends on the market session happening right now, and
    a browser is not the thing that knows that."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from api import computed

    chain = pd.DataFrame([
        dict(expiry="2026-09-04", side="CALL", strike=7700.0, iv=20.0,
             gamma=0.001, open_interest=100, dte=0),
    ])
    et = ZoneInfo("America/New_York")
    # 09:45 ET on an ordinary Friday -- the opening half hour, where the
    # collector polls every 60 seconds rather than every 300.
    out = computed.header(
        spx_price=7718.36, vix_value=None, prev_close=7700.0,
        session_open=None, chain_df=chain, snap_age_secs=10,
        now_et=datetime(2026, 9, 4, 9, 45, tzinfo=et),
    )
    assert out["session"] == "OPEN"
    assert (out["expected_interval"], out["amber_at"], out["red_at"]) == (60, 60, 90)
    assert out["market_closed"] is False

    # Overnight: no expectation at all, and the flag says so.
    shut = computed.header(
        spx_price=7718.36, vix_value=None, prev_close=7700.0,
        session_open=None, chain_df=chain, snap_age_secs=50_000,
        now_et=datetime(2026, 9, 4, 3, 0, tzinfo=et),
    )
    assert shut["market_closed"] is True
    assert shut["expected_interval"] is None


def test_the_percentile_band_says_mid_when_it_does_not_know():
    """NaN is no history at all. An unknown percentile must not paint green:
    HIGH is a claim that today is unusual, and "we have nothing to compare
    against" is not that claim."""
    import iv_engine

    assert iv_engine.percentile_band(float("nan")) == ("MID", "#6d8fa8")
    assert iv_engine.percentile_band(90.0)[0] == "HIGH"
    assert iv_engine.percentile_band(10.0)[0] == "LOW"
    # The boundaries are exclusive on both sides -- 25 and 75 are MID.
    assert iv_engine.percentile_band(25.0)[0] == "MID"
    assert iv_engine.percentile_band(75.0)[0] == "MID"


def test_an_empty_window_is_blank_not_a_zeroth_percentile():
    from api import computed

    out = computed.historical_window(pd.Series([], dtype=float), 1.0)
    assert out["low"] is None and out["high"] is None
    assert out["percentile"] is None
    assert out["band"] == "MID"
    assert out["observations"] == 0


def test_the_range_position_and_the_percentile_are_different_answers():
    """A ratio can sit halfway up its RANGE while being above 90% of its
    READINGS, and both are true. Served separately because a client deriving
    one from the other would get a plausible wrong number."""
    from api import computed

    # Nine readings bunched low, one outlier high. 0.5 is above eight of
    # ten readings, but only a quarter of the way up the range.
    ratios = pd.Series([0.1, 0.2, 0.2, 0.3, 0.3, 0.4, 0.4, 0.45, 0.5, 1.7])
    out = computed.historical_window(ratios, 0.5)
    assert out["percentile"] == pytest.approx(80.0)
    assert out["position_pct"] == pytest.approx(25.0)


# ── the expiry board and vGEX (Gamma tab, 2026-09-06) ───────────────────────

def test_the_expiry_board_says_which_windows_each_expiry_falls_in(client):
    """MEMBERSHIP TRAVELS, NOT FOUR CUTOFF DATES.

    Handing the browser the cutoffs would put a date comparison in TypeScript,
    evaluated in the VIEWER's timezone -- the same class of bug DEBT-030
    records for timestamps, and just as silent: a trader in London opening the
    tab at 01:00 would be shown the next day's cycle, and it would look
    entirely reasonable.
    """
    body = client.get("/mission/gamma").json()
    assert body["expiries"], "the picker cannot be built without these"

    keys = {f["key"] for f in body["expiry_filters"]}
    for option in body["expiries"]:
        assert set(option["filters"]) <= keys
        # Every caption the picker prints is the server's.
        for field in ("day_label", "dte_label", "call_label", "put_label"):
            assert isinstance(option[field], str) and option[field]


def test_a_board_figure_equals_the_bars_drawn_when_that_expiry_is_ticked(client):
    """The picker's numbers are only worth reading if they are the chart's.

    Checked end to end through the endpoint rather than in core/, because the
    board and the panel reach the caller down two different code paths and it
    is the SERVED pair that has to agree.
    """
    body = client.get("/mission/gamma").json()
    option = body["expiries"][0]

    scoped = client.get("/mission/gamma", params={"expiry": option["key"]}).json()
    assert (sum(r["call_gex"] for r in scoped["rows"])
            == pytest.approx(option["call_gex"]))
    assert (sum(r["put_gex"] for r in scoped["rows"])
            == pytest.approx(option["put_gex"]))


def test_two_expiries_can_be_asked_for_at_once_and_their_exposures_add(client):
    """`?expiry=a&expiry=b`, because the picker lets a trader tick several.

    A comma-joined string would arrive as ONE display key nothing matches and
    come back as an empty chain -- a thin-looking day rather than an error.
    """
    keys = [o["key"] for o in client.get("/mission/gamma").json()["expiries"]][:2]
    if len(keys) < 2:
        pytest.skip("this snapshot lists a single expiry")

    both = client.get("/mission/gamma", params={"expiry": keys}).json()
    apart = [client.get("/mission/gamma", params={"expiry": k}).json() for k in keys]

    assert both["expiry"] == keys
    assert (sum(r["call_gex"] for r in both["rows"])
            == pytest.approx(sum(sum(r["call_gex"] for r in b["rows"]) for b in apart)))


def test_vgex_and_gex_are_served_from_separate_cache_entries(client):
    """They share COLUMN NAMES on purpose, which is exactly the danger.

    One cache entry serving both would hand a caller the open-interest figures
    under the volume label, and every column name would still be right. That
    is the failure this endpoint's echoing of `measure` exists to prevent, and
    the only way to catch it is to ask for both in one process.
    """
    gamma = client.get("/mission/gamma?measure=gamma").json()
    vgex = client.get("/mission/gamma?measure=vgex").json()

    assert gamma["measure"] == "gamma" and vgex["measure"] == "vgex"
    assert "flow_ratio" in vgex and "flow_ratio" not in gamma
    # Asking again must not return the other one's answer.
    assert client.get("/mission/gamma?measure=gamma").json()["measure"] == "gamma"


def test_a_REPLAYED_snapshot_dates_itself_off_the_chain_and_not_off_today(
        client, temp_db):
    """A replayed snapshot must group against the day it was TAKEN.

    Using `date.today()` would drop every since-expired contract out of every
    window, so any snapshot older than a week -- which is most of what the
    record holds -- would show a picker with no filters on any row.

    IT ASKS FOR THE OLDER SNAPSHOT BY ID, and that is what makes it a test of
    REPLAY. Countdowns are now measured from the reader's own day on the
    CURRENT board, so that Sunday shows Tuesday's expiry as 2 DTE rather than
    the 4 it was when Friday's snapshot was taken (Chandan, 2026-09-06). This
    test used to reach that same board through the default route and so, after
    that change, was asserting the live rule while describing the replay one.
    A newer snapshot is inserted to push the seeded one into the past, which
    is the state the rule below actually exists for.

    `temp_db` is taken directly rather than read off the app, because a test
    that skips when it cannot find the path protects nothing at all.
    """
    from test_db import add_snapshot

    from dataaccess import queries

    replayed = client.get("/mission/gamma").json()["snapshot_id"]
    # Newer by the clock, and with no option rows of its own -- it exists only
    # to stop `replayed` being the newest thing in the record.
    add_snapshot(temp_db, "2099-01-02 15:00:00", spx=6000.0)

    body = client.get(f"/mission/gamma?snapshot_id={replayed}").json()
    chain = queries.load_chain_df(temp_db, replayed)
    taken = computed.snapshot_date(chain)

    assert taken is not None
    for option in body["expiries"]:
        # Nothing in the board predates the snapshot, and the near ones are
        # in at least one window -- which is the observable difference
        # between dating off the chain and dating off the wall clock.
        assert option["date"] >= taken.isoformat()
    assert any(option["filters"] for option in body["expiries"])
