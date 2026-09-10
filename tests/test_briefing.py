"""core/briefing.py — the ladder handed to a model, and the pin computed for it.

WHY THIS IS THE MORE IMPORTANT OF THE TWO TEST FILES. `test_llm.py` pins
plumbing: a wrong answer there is a briefing that does not arrive, which is
obvious. A wrong answer HERE is a briefing that arrives, reads fluently, and
is about numbers that were never true. Nothing downstream can detect that —
not the model, which has no other source, and not the reader, for whom the
whole point of the feature is that they did not have to read the chain
themselves.

THE PIN IS THE PART THAT MATTERS MOST. It is computed in Python precisely so
that a model cannot choose a level and then defend it, and every test below
about `pin_reading` is really one test asked several ways: does it decline to
produce a number when the data does not support one. A pin invented on a
short-gamma day would be the single most damaging output this feature could
produce, because it would look exactly like the days it is right.

BLANK IS NOT ZERO, and several tests exist only to pin that. A model told "0"
where a measure was unavailable will explain the zero — confidently, at
length, and about nothing.
"""
from __future__ import annotations

import datetime as dt
from typing import ClassVar

import pandas as pd

from core import briefing
from scripts import briefing_daemon


def _gex(rows: list[dict]) -> pd.DataFrame:
    """A frame shaped like `core.gex.by_strike`'s output."""
    return pd.DataFrame([
        {"strike": r["strike"],
         "call_gex": r.get("call_gex", 0.0),
         "put_gex": r.get("put_gex", 0.0),
         "net_gex": r.get("net_gex", 0.0),
         "abs_gex": r.get("abs_gex", 0.0),
         "call_oi": r.get("call_oi", 0.0),
         "put_oi": r.get("put_oi", 0.0),
         "call_volume": r.get("call_volume", 0.0),
         "put_volume": r.get("put_volume", 0.0)}
        for r in rows])


SPOT = 7700.0


# ── the pin ──────────────────────────────────────────────────────────────────

class TestPinPossible:
    """Whether the question is even asked, before which level answers it."""

    def test_short_gamma_offers_no_pin(self):
        """THE DAY THIS FEATURE WAS FIRST RUN, and every reading of it.

        Negative net gamma means dealer hedging follows the move rather than
        fading it. There is no magnet, and the correct output is that finding
        — not the least-bad strike on the board.
        """
        frame = _gex([{"strike": 7700, "net_gex": -5e9},
                      {"strike": 7705, "net_gex": 2e9}])
        reading = briefing.pin_reading(frame, SPOT, flip_strike=7706.0)
        assert reading["pin_possible"] is False
        assert reading["candidate"] == "—"
        assert reading["net_gex_sign"] == "short"
        assert "follows the move" in reading["reason"]

    def test_a_positive_board_names_the_biggest_near_strike(self):
        frame = _gex([{"strike": 7695, "net_gex": 1e9},
                      {"strike": 7700, "net_gex": 8e9},
                      {"strike": 7710, "net_gex": 3e9}])
        reading = briefing.pin_reading(frame, SPOT, flip_strike=7690.0)
        assert reading["pin_possible"] is True
        assert reading["candidate"] == "7,700"
        assert reading["net_gex_sign"] == "long"

    def test_a_distant_wall_is_not_a_pin(self):
        """A magnet four percent away is not pinning anything today; it is
        next week's story. The band is 1% and the strike below is outside it,
        so the board is long gamma with nothing near enough to hold spot."""
        far = SPOT * 1.03
        frame = _gex([{"strike": far, "net_gex": 9e9}])
        reading = briefing.pin_reading(frame, SPOT, flip_strike=None)
        assert reading["pin_possible"] is False
        assert reading["candidate"] == "—"
        # And it says WHY it is empty — a blank with no reason reads as a
        # failure to compute rather than as a finding.
        assert "within 1%" in reading["reason"]

    def test_a_long_board_with_no_positive_strike(self):
        """Can only happen on a frame whose sum is positive and whose rows are
        not — an all-NaN column, most likely. Reported as its own reason
        rather than sharing the distance one, because the fixes differ."""
        frame = _gex([{"strike": 7700, "net_gex": 0.0}])
        reading = briefing.pin_reading(frame, SPOT, flip_strike=None)
        assert reading["pin_possible"] is False


class TestWalls:
    """The range, which is two measured strikes and never an estimate."""

    def test_the_walls_straddle_spot(self):
        frame = _gex([{"strike": 7650, "net_gex": 4e9},
                      {"strike": 7700, "net_gex": 9e9},
                      {"strike": 7760, "net_gex": 6e9}])
        reading = briefing.pin_reading(frame, SPOT, flip_strike=7690.0)
        assert reading["support"] == "7,650"
        assert reading["resistance"] == "7,760"

    def test_a_missing_side_stays_blank(self):
        """AN ABSENT EDGE IS NOT SPOT. Falling back to the current price would
        draw a range out of no data and it would look like a measurement."""
        frame = _gex([{"strike": 7650, "net_gex": 4e9},
                      {"strike": 7700, "net_gex": 9e9}])
        reading = briefing.pin_reading(frame, SPOT, flip_strike=None)
        assert reading["support"] == "7,650"
        assert reading["resistance"] == "—"

    def test_negative_strikes_are_never_walls(self):
        """A short-gamma strike is where hedging AMPLIFIES a move. Treating it
        as an edge of the range would invert the meaning of the whole panel."""
        frame = _gex([{"strike": 7650, "net_gex": -9e9},
                      {"strike": 7690, "net_gex": 2e9},
                      {"strike": 7760, "net_gex": 8e9}])
        reading = briefing.pin_reading(frame, SPOT, flip_strike=None)
        assert reading["support"] == "7,690"

    def test_the_flip_inside_the_range_is_flagged(self):
        """The caveat on the whole reading: the regime CHANGES partway across
        the band, so its lower half does not behave like its upper half and
        the range must not be quoted as one thing."""
        frame = _gex([{"strike": 7650, "net_gex": 4e9},
                      {"strike": 7700, "net_gex": 9e9},
                      {"strike": 7760, "net_gex": 6e9}])
        inside = briefing.pin_reading(frame, SPOT, flip_strike=7690.0)
        assert inside["flip_inside_range"] is True
        outside = briefing.pin_reading(frame, SPOT, flip_strike=7900.0)
        assert outside["flip_inside_range"] is False

    def test_no_flip_leaves_the_caveat_unanswered(self):
        """None, not False. "The flip is not inside the range" and "there is
        no flip" are different statements and only one of them is a fact."""
        frame = _gex([{"strike": 7650, "net_gex": 4e9},
                      {"strike": 7760, "net_gex": 6e9}])
        assert briefing.pin_reading(
            frame, SPOT, flip_strike=None)["flip_inside_range"] is None


class TestPinEdges:

    def test_an_empty_board_is_not_a_pin(self):
        reading = briefing.pin_reading(_gex([]), SPOT, flip_strike=None)
        assert reading["pin_possible"] is False
        assert reading["candidate"] == "—"

    def test_rows_missing_gamma_are_dropped_not_zeroed(self):
        """A strike with no gamma is unknown, not flat. Filled with zero it
        would drag the board's sign toward neutral and could flip the whole
        pin verdict."""
        frame = _gex([{"strike": 7700, "net_gex": None},
                      {"strike": 7705, "net_gex": 8e9}])
        reading = briefing.pin_reading(frame, SPOT, flip_strike=None)
        assert reading["candidate"] == "7,705"

    def test_an_all_blank_board_is_not_a_pin(self):
        frame = _gex([{"strike": 7700, "net_gex": None}])
        assert briefing.pin_reading(
            frame, SPOT, flip_strike=None)["pin_possible"] is False


# ── the rungs ────────────────────────────────────────────────────────────────

class TestVolumeRung:

    def test_the_busiest_strikes_lead(self):
        frame = _gex([
            {"strike": 7700, "call_volume": 6879, "put_volume": 9289},
            {"strike": 7650, "call_volume": 272, "put_volume": 14359},
            {"strike": 7500, "call_volume": 10, "put_volume": 10},
        ])
        figures = briefing.volume_figures(frame, top=2)
        assert [row["strike"] for row in figures["busiest"]] == ["7,700", "7,650"]
        assert figures["busiest"][0]["contracts"] == "16,168"

    def test_the_put_call_ratio_is_puts_over_calls(self):
        frame = _gex([{"strike": 7700, "call_volume": 100, "put_volume": 150}])
        assert briefing.volume_figures(frame)["put_call_ratio"] == "1.50"

    def test_no_calls_traded_is_a_blank_ratio_not_an_infinite_one(self):
        """A ratio with a zero denominator is not a large ratio; it is no
        ratio. "inf" in a prompt is a number a model will try to explain."""
        frame = _gex([{"strike": 7700, "call_volume": 0, "put_volume": 150}])
        assert briefing.volume_figures(frame)["put_call_ratio"] == "—"

    def test_an_empty_board_blanks_every_figure(self):
        figures = briefing.volume_figures(_gex([]))
        assert figures["busiest"] == []
        assert figures["call_volume"] == "—"


class TestDeltaRung:

    def test_the_peak_is_by_magnitude_not_by_sign(self):
        """The largest exposure is the largest either way. Taking the maximum
        rather than the maximum absolute would report the biggest POSITIVE
        strike and silently ignore a larger negative one."""
        frame = pd.DataFrame([{"strike": 7500, "net_dex": -9e9},
                              {"strike": 7700, "net_dex": 4e9}])
        assert briefing.delta_figures(frame)["peak_dex_strike"] == "7,500"

    def test_an_empty_frame_blanks_rather_than_raising(self):
        figures = briefing.delta_figures(pd.DataFrame())
        assert figures == {"net_dex": "—", "peak_dex_strike": "—"}


class TestSecondOrderRungs:
    """`core.gex.second_order_summary` returns one fixed six-key dict with the
    unasked half set to None. Carrying all six into a prompt hands a model
    three em dashes under a heading and lets it wonder aloud what they mean."""

    SUMMARY: ClassVar[dict] = {"net_vex": 7.6e9, "abs_vex": 23.2e9, "peak_vex_strike": 7500.0,
               "net_cex": None, "abs_cex": None, "peak_cex_strike": None}

    def test_vanna_carries_only_vanna(self):
        figures = briefing.second_order_figures(self.SUMMARY, "vanna")
        assert set(figures) == {"net_vex", "abs_vex", "peak_vex_strike"}
        assert figures["peak_vex_strike"] == "7,500"

    def test_charm_carries_only_charm(self):
        summary = {**self.SUMMARY, "net_cex": 2.1e9, "abs_cex": 52.8e9,
                   "peak_cex_strike": 7650.0}
        figures = briefing.second_order_figures(summary, "charm")
        assert set(figures) == {"net_cex", "abs_cex", "peak_cex_strike"}

    def test_a_missing_figure_is_an_em_dash(self):
        figures = briefing.second_order_figures(self.SUMMARY, "charm")
        assert figures["peak_cex_strike"] == "—"

    def test_no_summary_is_no_figures(self):
        assert briefing.second_order_figures(None, "vanna") == {}


class TestGammaRung:

    SUMMARY: ClassVar[dict] = {"net_gex": -21.0e9, "call_gex": 146.0e9, "put_gex": 166.9e9,
               "abs_gex": 312.9e9, "ratio": -1.7, "sentiment": 44.0,
               "positive_bars": 44, "total_bars": 101,
               "peak_strike": 7700.0, "peak_side": "Put"}

    def test_the_peak_carries_its_side(self):
        """"7,700" and "7,700 (Put)" are different claims, and the second is
        the one the screen makes."""
        figures = briefing.gamma_figures(self.SUMMARY, flip_strike=7706.0)
        assert "Put" in figures["peak_strike"]

    def test_the_sentiment_carries_its_bar_count(self):
        """51% of 101 strikes and 51% of 2 are different statements. The
        percentage alone reads as a confidence."""
        figures = briefing.gamma_figures(self.SUMMARY, flip_strike=None)
        assert figures["sentiment"] == "44% (44/101)"

    def test_the_ratio_is_signed(self):
        """Its sign is which side WON, and dropping it turns a red figure and
        a green one into the same number."""
        assert briefing.gamma_figures(
            self.SUMMARY, flip_strike=None)["ratio"] == "-1.7x"

    def test_an_uncomputed_flip_is_an_em_dash(self):
        assert briefing.gamma_figures(
            self.SUMMARY, flip_strike=None)["flip_strike"] == "—"

    def test_no_summary_is_no_figures(self):
        assert briefing.gamma_figures(None, flip_strike=7700.0) == {}


# ── the whole ladder ─────────────────────────────────────────────────────────

class TestAssemble:

    def _payloads(self, frame):
        summary = {"net_gex": -21e9, "call_gex": 146e9, "put_gex": 166.9e9,
                   "abs_gex": 312.9e9, "ratio": -1.7, "sentiment": 44.0,
                   "positive_bars": 44, "total_bars": 101,
                   "peak_strike": 7700.0, "peak_side": "Put"}
        second = {"net_vex": 7.6e9, "abs_vex": 23.2e9,
                  "peak_vex_strike": 7500.0, "net_cex": 2.1e9,
                  "abs_cex": 52.8e9, "peak_cex_strike": 7650.0}
        return dict(
            gamma={"summary": summary, "flip_strike": 7706.0,
                   "by_strike": frame},
            vgex={"summary": summary, "flip_strike": 7707.0,
                  "by_strike": frame},
            delta={"by_strike": pd.DataFrame(
                [{"strike": 7500.0, "net_dex": 40.4e9}])},
            vanna={"summary": second}, charm={"summary": second})

    def test_the_rungs_arrive_in_reading_order(self):
        """THE ORDER IS THE WHOLE POINT. Each rung is a derivative of the one
        above it, and a model handed all five at once leads with charm because
        charm sounds sophisticated."""
        frame = _gex([{"strike": 7700, "net_gex": 8e9, "call_volume": 10,
                       "put_volume": 20}])
        built = briefing.assemble(spot=SPOT, session_time="09:45",
                                  **self._payloads(frame))
        assert [rung["name"] for rung in built["ladder"]] == [
            "volume", "delta", "gamma", "vgex", "vanna", "charm"]

    def test_every_rung_states_what_it_measures(self):
        """A number in a prompt with no statement of its basis is a number a
        model will describe in whatever terms sound most confident."""
        frame = _gex([{"strike": 7700, "net_gex": 8e9}])
        built = briefing.assemble(spot=SPOT, session_time="09:45",
                                  **self._payloads(frame))
        assert all(rung["basis"].strip() for rung in built["ladder"])

    def test_the_volume_basis_denies_direction(self):
        """The single most likely misreading of this whole feature: a model
        shown "calls 105k / puts 154k" narrating it as buying and selling.
        The chain records HOW MANY traded and never who was the aggressor."""
        frame = _gex([{"strike": 7700, "net_gex": 8e9}])
        built = briefing.assemble(spot=SPOT, session_time="09:45",
                                  **self._payloads(frame))
        volume = next(r for r in built["ladder"] if r["name"] == "volume")
        assert "never who was the buyer" in volume["basis"]

    def test_the_pin_travels_with_its_own_basis(self):
        frame = _gex([{"strike": 7700, "net_gex": 8e9}])
        built = briefing.assemble(spot=SPOT, session_time="09:45",
                                  **self._payloads(frame))
        assert "not judged" in built["pin"]["basis"]

    def test_the_ladder_constant_matches_what_is_built(self):
        """LADDER is named once so the assembler, the prompt and any test
        asking "did we show all five" cannot drift apart."""
        frame = _gex([{"strike": 7700, "net_gex": 8e9}])
        built = briefing.assemble(spot=SPOT, session_time="09:45",
                                  **self._payloads(frame))
        names = {rung["name"] for rung in built["ladder"]}
        assert set(briefing.LADDER) <= names


# ── the daemon's schedule ────────────────────────────────────────────────────

class TestSchedule:
    """`scripts/briefing_daemon.slots_for` — a pure function of the day.

    THE WHOLE REASON THE SCHEDULE IS CODE rather than a scheduler's
    configuration. A Task Scheduler XML cannot be asked "what would you do on
    Christmas Day" without waiting for Christmas Day; this can.
    """

    def test_a_weekend_has_no_slots(self):
        assert briefing_daemon.slots_for(dt.date(2026, 9, 5), set()) == []

    def test_a_holiday_has_no_slots(self):
        """The COLLECTOR's holiday rule, reused rather than restated. A daemon
        keeping its own list would eventually disagree with the process
        producing the data it reads, and nothing would report it."""
        assert briefing_daemon.slots_for(dt.date(2026, 9, 7),
                                         {"2026-09-07"}) == []

    def test_a_trading_day_opens_at_0945_and_closes_at_1600(self):
        slots = briefing_daemon.slots_for(dt.date(2026, 9, 8), set())
        assert slots[0] == dt.time(9, 45)
        assert slots[-1] == dt.time(16, 0)

    def test_the_close_is_denser_than_the_middle(self):
        """0DTE charm goes vertical in the last half hour, and an even grid
        would spend its resolution on the quietest part of the day instead."""
        slots = briefing_daemon.slots_for(dt.date(2026, 9, 8), set())
        midday = [s for s in slots if dt.time(11) <= s < dt.time(14)]
        closing = [s for s in slots if s >= dt.time(15)]
        assert len(closing) > len(midday) / 2

    def test_no_slot_is_scheduled_twice(self):
        slots = briefing_daemon.slots_for(dt.date(2026, 9, 8), set())
        assert len(slots) == len(set(slots))


class TestSlotIsDue:
    """The grace window: how late is too late to still be describing now."""

    def test_a_slot_is_due_at_its_minute(self):
        now = dt.datetime(2026, 9, 8, 9, 45, tzinfo=briefing_daemon._MARKET_TZ)
        assert briefing_daemon._due(now, dt.time(9, 45))

    def test_a_slot_is_not_due_before_it(self):
        now = dt.datetime(2026, 9, 8, 9, 44, tzinfo=briefing_daemon._MARKET_TZ)
        assert not briefing_daemon._due(now, dt.time(9, 45))

    def test_a_stale_slot_is_skipped_not_run_late(self):
        """A briefing filed under 09:45 that was written at 10:20 would be
        graded by the scoring log as though it had been read at 09:45."""
        now = dt.datetime(2026, 9, 8, 10, 20, tzinfo=briefing_daemon._MARKET_TZ)
        assert not briefing_daemon._due(now, dt.time(9, 45))
