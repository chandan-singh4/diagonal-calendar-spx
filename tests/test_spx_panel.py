"""The SPX panel outlives the marks above it (Chandan, 2026-09-07).

THE FAULT THESE PIN. `get_transform_mark_history` drops any snapshot missing
one of the six option legs, which is right — a diagonal cannot be priced
without all six. But `s.underlying_price` was a column on that same row, so
the index inherited an exclusion rule that has nothing to do with it. On a
0DTE afternoon the front legs stop being quoted around 15:00, and the
strike-channel panel ended an hour before the market did:

    "the SPX, I mean, that's just the market data. That goes to four PM
     irrespective of what? So why do I not see the SPX till four PM?"

Measured on the real record for Put 7620 / Call 7720, front 2026-09-04:
the last marks row is 14:50; SPX is present in all 42 snapshots to 16:01.
"""
from __future__ import annotations

import pytest

import db
from test_db import (
    BACK,
    CALL_STRIKE,
    FRONT,
    PUT_STRIKE,
    add_snapshot,
    atm,
    four_legs,
    ts_ago,
    wing_legs,
)


def _session_with_a_dead_afternoon(path: str) -> tuple[str, str]:
    """Two snapshots: one fully priced, one where a front leg went unquoted.

    Returns (fully_priced_stamp, unquoted_stamp) — the shape of a 0DTE
    afternoon, which is the only shape this fix is about.
    """
    early = ts_ago(days=1)
    late = ts_ago(days=0)

    priced = add_snapshot(path, early, spx=6000.0)
    db.insert_option_rows(path, four_legs(priced) + wing_legs(priced))
    db.insert_atm_iv_records(path, [atm(priced, FRONT, 0.20), atm(priced, BACK, 0.10)])

    # The front put stops being priceable — no mark, no bid, no ask. Everything
    # else about the snapshot is intact, including the index.
    unquoted = add_snapshot(path, late, spx=6042.5)
    legs = four_legs(unquoted) + wing_legs(unquoted)
    legs = [leg for leg in legs
            if not (leg["expiry_date"] == FRONT and leg["strike"] == PUT_STRIKE
                    and leg["right"] == "P")]
    db.insert_option_rows(path, legs)
    db.insert_atm_iv_records(path, [atm(unquoted, FRONT, 0.20), atm(unquoted, BACK, 0.10)])

    return early, late


class TestTheQuery:

    def test_the_marks_still_drop_the_snapshot_that_lost_a_leg(self, temp_db):
        """The exclusion rule is CORRECT and must survive this fix. A diagonal
        with five of six legs is not a cheap diagonal, it is not a diagonal."""
        early, _late = _session_with_a_dead_afternoon(temp_db)
        rows = db.get_transform_mark_history(temp_db, FRONT, BACK,
                                             CALL_STRIKE, PUT_STRIKE, days=30)
        assert [r["snapshot_timestamp"] for r in rows] == [early]

    def test_the_index_survives_the_snapshot_the_marks_dropped(self, temp_db):
        """The whole fix, in one assertion."""
        early, late = _session_with_a_dead_afternoon(temp_db)
        rows = db.get_underlying_history(temp_db, days=30)
        assert [r["snapshot_timestamp"] for r in rows] == [early, late]
        assert rows[-1]["spx"] == pytest.approx(6042.5)

    def test_it_needs_no_options_at_all(self, temp_db):
        """A snapshot with an empty chain still recorded where the index was.
        Nothing about SPX depends on a strike pair, so nothing here does."""
        add_snapshot(temp_db, ts_ago(days=1), spx=5987.25)
        rows = db.get_underlying_history(temp_db, days=30)
        assert len(rows) == 1
        assert rows[0]["spx"] == pytest.approx(5987.25)

    def test_an_incomplete_snapshot_is_not_the_index(self, temp_db):
        """Same completeness rule as the marks, so the two series cover the
        same sessions. A PARTIAL snapshot is a collection that did not finish."""
        add_snapshot(temp_db, ts_ago(days=1), spx=6000.0)
        add_snapshot(temp_db, ts_ago(days=0), spx=6100.0, status="PARTIAL")
        rows = db.get_underlying_history(temp_db, days=30)
        assert [r["spx"] for r in rows] == [pytest.approx(6000.0)]

    def test_a_snapshot_with_no_price_is_left_out_rather_than_drawn_at_zero(self, temp_db):
        """Missing price → blank, not 0. A zero would draw the index falling
        to the axis, which reads as a crash rather than as a gap."""
        add_snapshot(temp_db, ts_ago(days=1), spx=6000.0)
        add_snapshot(temp_db, ts_ago(days=0), spx=None)
        rows = db.get_underlying_history(temp_db, days=30)
        assert [r["spx"] for r in rows] == [pytest.approx(6000.0)]


class TestWhatTheChartIsServed:

    @pytest.fixture
    def served(self, temp_db):
        from fastapi.testclient import TestClient

        from api.app import create_app
        _session_with_a_dead_afternoon(temp_db)
        client = TestClient(create_app(db_path=temp_db))
        return client.get(
            f"/pairs/transform-marks?front={FRONT}&back={BACK}"
            f"&call_strike={CALL_STRIKE}&put_strike={PUT_STRIKE}&days=30"
        ).json()

    def test_the_index_series_outlives_the_marks(self, served):
        assert len(served["spx_rows"]) > served["count"]
        assert served["spx_rows"][-1]["timestamp"] > served["rows"][-1]["timestamp"]

    def test_the_index_arrives_as_wall_clock_like_every_other_series(self, served):
        """DEBT-030: Plotly's rangebreaks position points by the clock part and
        ignore the zone, so a zoned stamp here would draw the panel at a
        different hour from the chart above it."""
        stamp = served["spx_rows"][-1]["timestamp"]
        assert "+" not in stamp and not stamp.endswith("Z")

    def test_the_axis_is_drawn_on_the_series_that_reaches_the_close(self, served):
        """Anchoring the window to the marks would shorten the whole figure to
        wherever the front legs gave out."""
        assert served["session_axis_range"] is not None
        assert served["session_axis_range"][1].endswith("16:15")
