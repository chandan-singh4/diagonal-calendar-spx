"""
Golden/characterization tests for _candidate_signals — the DB-reading half of
the M2 safety net (DEBT-026, task 2.0b).

WHAT THIS COVERS
----------------
_candidate_signals() produces four things you see on every Mission Control card:

    duration     "Duration Active — 2h 12m"   how long the gap has held >= $5
    eta_minutes  "~18 min"                    projected time until it crosses $5
    spark        "▁▂▄▆█"                      the recent gap trajectory
    trend_up     the rising-trend indicator

Unlike test_display_golden.py, this one needs a real database, because the
function reads snapshots. It gets a temporary one built through db.py's own
writers (see conftest's make_transform_history) — never the production database,
which conftest forbids outright.

WHY THIS MATTERS MORE THAN IT LOOKS
-----------------------------------
None of these four numbers crashes when it goes wrong. The card still renders,
still says "2h 12m", and is still plausible. Duration in particular is measured
by walking BACKWARDS from the newest row through a contiguous streak, so a
disturbance anywhere in the pipeline yields a confidently wrong number with
nothing on screen to suggest it.

A CORRECTION WORTH KEEPING, because it changes what protects what
-----------------------------------------------------------------
An earlier version of this docstring claimed the headline M2 risk was
`df.sort_values("timestamp")` being dropped during the split into
data/queries.py — "the SQL already has an ORDER BY". Injecting exactly that
changed nothing: all 19 tests still passed.

Investigating rather than adding a test to force it red: the pandas sort is
genuinely REDUNDANT. `get_transform_mark_history` ends in `ORDER BY
s.snapshot_timestamp`, and that clause is load-bearing — stripping it returns
rows in insertion order, which for out-of-sequence snapshots is not time order.
It is also already pinned, in test_db.py::
test_transform_mark_history_is_ordered_oldest_first, which inserts snapshots
1, 3 and 2 days back specifically so insertion order and time order disagree.

So the protection is real; it just lives in the query's tests, not here. The
pandas sort is defence-in-depth. Recorded because "an injection that changes
nothing is evidence about the code, not a dud test" (ADR-027), and because the
useful instruction for M2 is different from what was written first: when that
query moves, **its ordering test must move with it.**

WHAT THIS DOES NOT CLAIM
------------------------
Same limit as the other golden files: nothing here says the current behaviour is
CORRECT. The ETA is a straight-line projection from at most six readings, which
is a modelling choice, not a truth. These tests freeze it so M2 cannot change it
by accident.

THE ONE UNAVOIDABLE UGLINESS — NO LONGER UNAVOIDABLE
----------------------------------------------------
_candidate_signals used to read config.DB_PATH — a module global — rather than
taking a path argument, so every test here had to monkeypatch it: a test
modifying the thing it was testing. That was DEBT-027, and M2 step 2.2 fixed it
(ADR-033): the function now takes `db_path`, defaulting to the global.

The `signals` fixture below still monkeypatches, deliberately. Production calls
these without the argument, so the default path is the one that has to keep
working, and leaving 22 tests exercising it is the cheapest way to be sure.
`test_the_database_location_can_be_given_instead_of_patched` covers the new
argument, and is the only test here that proves the fix.
"""
from __future__ import annotations

import pandas as pd
import pytest
from app_loader import load_mission_control_functions
from conftest import MC_BACK_EXPIRY, MC_CALL_STRIKE, MC_FRONT_EXPIRY, MC_PUT_STRIKE

import config

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def mc() -> dict:
    return load_mission_control_functions()


@pytest.fixture
def signals(mc, monkeypatch):
    """Call _candidate_signals against a given temporary database."""
    def call(db_path, **kwargs):
        monkeypatch.setattr(config, "DB_PATH", db_path)
        return mc["_candidate_signals"](
            MC_FRONT_EXPIRY, MC_BACK_EXPIRY, MC_PUT_STRIKE, MC_CALL_STRIKE, **kwargs
        )

    return call


def _minutes(td) -> float:
    return td.total_seconds() / 60.0


def test_the_database_location_can_be_given_instead_of_patched(mc, mc_db, tmp_path,
                                                               monkeypatch):
    """DEBT-027, fixed in M2 step 2.2 (ADR-033) — and this is what proves it.

    config.DB_PATH is aimed at a file that does not exist; the argument is aimed
    at the fixture. Getting signals back is only possible if the argument won.
    Without this, the new parameter could quietly be ignored and every other
    test here would still pass, because they all set the global.
    """
    db_path, write = mc_db
    write([6.0, 6.0, 6.0])
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "no-such-database.db")

    out = mc["_candidate_signals"](
        MC_FRONT_EXPIRY, MC_BACK_EXPIRY, MC_PUT_STRIKE, MC_CALL_STRIKE,
        db_path=db_path,
    )

    assert out is not None, "the db_path argument was ignored"
    assert out["spark"], "read the right database but produced nothing"


# ═══════════════════════════════════════════════════════════════════════════════
# The fixture itself must be trustworthy before anything built on it counts
# ═══════════════════════════════════════════════════════════════════════════════

def test_fixture_produces_the_gaps_it_claims(mc_db, signals):
    """If the six-leg arithmetic is wrong, every other test here is meaningless.

    The gap is engineered to equal the front call mark exactly (see conftest), so
    a series of gaps must come back as that same series in the sparkline's shape.
    A flat request must read flat; a rising one must read rising.
    """
    db_path, write = mc_db
    write([1.0, 1.0, 1.0])
    flat = signals(db_path)
    assert flat is not None, "the fixture wrote no rows the query could see"
    assert len(set(flat["spark"])) == 1, f"a flat series must read flat, got {flat['spark']!r}"


def test_no_history_returns_nothing_rather_than_zeros(mc_db, signals):
    """An empty database must give None, not a card full of zeros — otherwise a
    combo with no history would render as a real opportunity at $0."""
    db_path, _ = mc_db
    assert signals(db_path) is None


def test_partial_snapshots_are_excluded(mc_db, signals):
    """A snapshot the collector never finished must not reach the screen."""
    db_path, write = mc_db
    write([1.0, 9.0, 9.0], incomplete_indices=(1, 2))
    result = signals(db_path)
    # Only the first (COMPLETE, gap 1.0) survives, so nothing is active.
    assert result["duration"] is None


def test_snapshots_missing_a_leg_are_excluded(mc_db, signals):
    """The dangerous exclusion case: the snapshot is COMPLETE and looks healthy,
    but one of the six legs has no computable mark, so no transform price exists.

    Including such a row would put a partially-priced structure on a card as if
    it were fully quoted.
    """
    db_path, write = mc_db
    write([9.0, 9.0, 9.0], missing_leg_indices=(0, 1))
    result = signals(db_path)
    assert result is not None
    # One usable snapshot remains, so the streak is zero-length, not 10 minutes.
    assert result["duration"] == pd.Timedelta(0)


# ═══════════════════════════════════════════════════════════════════════════════
# Duration Active — how long the gap has held above the $5 threshold
# ═══════════════════════════════════════════════════════════════════════════════

def test_duration_measures_only_the_trailing_streak(mc_db, signals):
    """The streak must be the one ending NOW, not the longest one in the window.

    Here the gap was above $5 for four early snapshots, dropped below, then came
    back for the last two. The answer is the recent 5 minutes, not the earlier 15.
    """
    db_path, write = mc_db
    write([6.0, 6.0, 6.0, 6.0, 1.0, 6.0, 6.0], interval_minutes=5)
    result = signals(db_path)
    assert _minutes(result["duration"]) == pytest.approx(5.0, abs=0.1)


def test_duration_is_none_when_not_currently_eligible(mc_db, signals):
    """A gap that WAS active but has fallen back must show no duration at all —
    'active for 2h' next to a $1 gap would be actively misleading."""
    db_path, write = mc_db
    write([9.0, 9.0, 9.0, 1.0])
    assert signals(db_path)["duration"] is None


def test_duration_spans_the_whole_window_when_always_eligible(mc_db, signals):
    db_path, write = mc_db
    write([6.0] * 7, interval_minutes=10)
    result = signals(db_path)
    assert _minutes(result["duration"]) == pytest.approx(60.0, abs=0.1)


def test_the_threshold_is_inclusive(mc_db, signals):
    """Exactly $5.00 counts as active (`gap >= _TSCAN_THRESHOLD`).

    Pinned because it is a boundary someone could flip to `>` while tidying, and
    it decides whether a card appears at all.
    """
    db_path, write = mc_db
    write([5.0, 5.0])
    assert signals(db_path)["duration"] is not None


def test_duration_survives_a_collector_outage_in_the_streak(mc_db, signals):
    """PINS ARGUABLY-WRONG BEHAVIOUR, deliberately.

    The streak is contiguous in SNAPSHOTS, not in time. With a 90-minute gap
    between polls — a collector outage — the two readings either side are treated
    as one unbroken streak, so the duration spans the outage as though the gap
    had been observed holding throughout. It was not observed at all.

    Frozen rather than fixed because the alternative (break the streak on a time
    gap, as _break_sessions does for charts) is a judgment call about what
    "active" should mean, and it belongs to whoever owns that decision — not to a
    refactor. If someone changes it, this test goes red and they read this note.
    """
    db_path, write = mc_db
    write([6.0, 6.0], interval_minutes=90)
    result = signals(db_path)
    assert _minutes(result["duration"]) == pytest.approx(90.0, abs=0.1)


# ═══════════════════════════════════════════════════════════════════════════════
# ETA — the straight-line projection to $5
# ═══════════════════════════════════════════════════════════════════════════════

def test_eta_projects_a_rising_gap_to_the_threshold(mc_db, signals):
    """Rising $1 per 5-minute poll from $2 means $5 is three polls away."""
    db_path, write = mc_db
    write([1.0, 2.0, 3.0, 4.0], interval_minutes=5)
    result = signals(db_path)
    assert result["eta_minutes"] == pytest.approx(5.0, abs=0.3)


def test_no_eta_once_already_eligible(mc_db, signals):
    """An ETA to a threshold already crossed is meaningless — the card shows a
    duration instead."""
    db_path, write = mc_db
    write([5.5, 6.0, 6.5])
    assert signals(db_path)["eta_minutes"] is None


def test_no_eta_for_a_falling_gap(mc_db, signals):
    """A declining gap must not extrapolate to a negative or bogus arrival time;
    the code returns None rather than inventing one."""
    db_path, write = mc_db
    write([4.0, 3.0, 2.0, 1.0])
    assert signals(db_path)["eta_minutes"] is None


def test_no_eta_for_a_flat_gap(mc_db, signals):
    """A flat series has slope ~0; projecting it would divide by nearly nothing
    and produce an ETA of centuries."""
    db_path, write = mc_db
    write([2.0, 2.0, 2.0, 2.0])
    assert signals(db_path)["eta_minutes"] is None


def test_no_eta_from_too_little_history(mc_db, signals):
    """Fewer than three readings cannot support a slope. Two points would always
    fit a line perfectly and report a confident ETA from noise."""
    db_path, write = mc_db
    write([1.0, 3.0])
    assert signals(db_path)["eta_minutes"] is None


def test_eta_uses_only_the_recent_tail(mc_db, signals):
    """The slope comes from the last SIX readings only, so an older opposite
    trend must not drag the projection.

    The first three readings are a steep decline and sit outside the window. The
    last six rise by $0.50 every 5 minutes, ending at $4.00 — so $5.00 is 10
    minutes away, and the earlier collapse must not enter the arithmetic.

    Getting this test's own data wrong is easy and was: an earlier version put
    part of the decline inside the six-reading tail and then asserted the ETA was
    short. The code was right and the test was wrong.
    """
    db_path, write = mc_db
    old_decline = [9.0, 8.0, 7.0]                      # outside the 6-reading window
    recent_rise = [1.5, 2.0, 2.5, 3.0, 3.5, 4.0]       # +0.50 per 5 min -> $5 in 10 min
    write([*old_decline, *recent_rise], interval_minutes=5)
    result = signals(db_path)
    assert result["eta_minutes"] == pytest.approx(10.0, abs=0.5)


# ═══════════════════════════════════════════════════════════════════════════════
# Sparkline and trend — the at-a-glance shape
# ═══════════════════════════════════════════════════════════════════════════════

def test_trend_up_needs_three_rising_readings(mc_db, signals):
    db_path, write = mc_db
    write([1.0, 2.0, 3.0])
    assert signals(db_path)["trend_up"] is True


def test_trend_up_is_false_when_the_last_reading_turns_down(mc_db, signals):
    """The indicator must react to the turn, not to the overall shape — a gap
    that has peaked is the moment the trader most needs to not see an up arrow.
    """
    db_path, write = mc_db
    write([1.0, 5.0, 9.0, 8.0])
    assert signals(db_path)["trend_up"] is False


def test_trend_up_needs_all_three_of_the_last_three_rising(mc_db, signals):
    """A dip-then-recover must NOT read as a rising trend.

    The last three readings are 5.0, 4.0, 4.5 — up on the final step but not
    monotonic across three. Chosen because it is the only shape that separates
    "look at three readings" from "look at two": most series answer both the same
    way, so a test using one of those would pass whether the window were 3 or 2.
    """
    db_path, write = mc_db
    write([1.0, 2.0, 5.0, 4.0, 4.5])
    assert signals(db_path)["trend_up"] is False


def test_sparkline_tracks_the_gap_series(mc_db, signals):
    """The glyph must rise when the gap rises and peak where the gap peaked."""
    db_path, write = mc_db
    write([1.0, 2.0, 3.0, 4.0, 5.0, 4.0, 3.0, 2.0])
    spark = signals(db_path)["spark"]
    peak = spark.index(max(spark))
    assert 0 < peak < len(spark) - 1, f"peak should be mid-glyph, got {spark!r}"


def test_sparkline_shows_the_MOST_RECENT_readings(mc_db, signals):
    """The glyph is the last 12 readings, not the first 12.

    16 readings: twelve flat at $1, then a clean climb to $4.90. Reading from the
    front would show a flat line while the gap was in fact about to cross the
    threshold — the single most misleading thing this glyph could do.

    A shorter series cannot catch this, because with 12 or fewer readings the
    first 12 and the last 12 are the same twelve. That is why the earlier
    8-reading test above passed against a deliberately reversed implementation.
    """
    db_path, write = mc_db
    write([1.0] * 12 + [2.0, 3.0, 4.0, 4.9])
    spark = signals(db_path)["spark"]

    assert len(set(spark)) > 1, f"a climbing series must not read as flat: {spark!r}"
    assert spark[-1] == max(spark), (
        f"the newest reading is the highest, so the glyph must end at its peak: {spark!r}"
    )


def test_history_window_is_respected(mc_db, signals):
    """`days` bounds the query. History older than the window must not appear —
    this is the parameter that keeps a card about today from being shaped by
    last week.
    """
    db_path, write = mc_db
    # Place the entire series ~3 days back, then ask for 1 day.
    write([9.0, 9.0, 9.0], end_minutes_ago=3 * 24 * 60)
    assert signals(db_path, days=1) is None
    # The same data IS visible with a wide enough window.
    assert signals(db_path, days=7) is not None
