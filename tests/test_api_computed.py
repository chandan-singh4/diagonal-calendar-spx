"""M4.3 — the "New" flag, moved out of a browser tab and into the record.

THE BEHAVIOUR BEING PINNED. A pair is new when it is eligible now and was not
eligible at the previous RECORDED snapshot. Everything below is a way of
asking whether that sentence holds at its edges, because every edge here has a
plausible wrong answer that looks fine in normal use:

  * first ever recording — nothing can be new, there is no "before"
  * a snapshot where nothing qualified — must be distinguishable from one
    never examined, or the next comparison silently reaches too far back
  * the same request twice — must not report new the first time and nothing
    the second
  * a pair that leaves and returns — new again, because it was absent

The key format is checked against services/mission_control.py's, since the two
have to agree or the flag compares two vocabularies that never intersect.
"""
from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

import db
from api import computed


@pytest.fixture
def registry_db(temp_db) -> str:
    return temp_db


def _sweep(*pairs) -> pd.DataFrame:
    """A scanner-shaped frame. Columns match core/scanner.py's output."""
    return pd.DataFrame([
        {"Front Expiry": f"{front} (7d)", "Back Expiry": f"{back} (28d)",
         "Put Strike": put, "Call Strike": call, "Transform Diff": gap}
        for front, back, put, call, gap in pairs
    ])


# ─────────────────────────────────────────────────────────────────────────────
# The key has to match what the page already writes
# ─────────────────────────────────────────────────────────────────────────────

def test_the_pair_key_matches_the_registry_format():
    """services/mission_control.py has written "front|back|put|call" with the
    strikes as ints since M2. A key differing by a decimal point would make
    every pair new forever."""
    assert computed.pair_key("2026-09-11", "2026-10-02", 7700.0, 7750.0) == \
        "2026-09-11|2026-10-02|7700|7750"


def test_the_expiry_label_is_stripped_to_a_date():
    """The scanner emits "2026-09-11 (7d)"; the key uses the date alone,
    exactly as `.split(" ")[0]` does in services/."""
    sweep = _sweep(("2026-09-11", "2026-10-02", 7700, 7750, 6.0))

    assert list(computed.eligible_from_sweep(sweep)) == \
        ["2026-09-11|2026-10-02|7700|7750"]


def test_only_pairs_at_or_above_the_threshold_are_eligible():
    """`>=`, matching services/. A gap of exactly the threshold is eligible,
    not approaching."""
    sweep = _sweep(
        ("2026-09-11", "2026-10-02", 7700, 7750, 5.0),   # exactly — in
        ("2026-09-11", "2026-10-02", 7600, 7650, 4.99),  # just under — out
    )

    eligible = computed.eligible_from_sweep(sweep)

    assert list(eligible) == ["2026-09-11|2026-10-02|7700|7750"]


# ─────────────────────────────────────────────────────────────────────────────
# The diff itself
# ─────────────────────────────────────────────────────────────────────────────

def test_nothing_is_new_on_the_first_ever_recording(registry_db):
    """There is no "before" for anything to have been absent from. Calling
    everything new here is the false alarm the browser-tab version raises
    every time a tab is reopened."""
    result = computed.new_since_previous(registry_db, 100, {"a|b|1|2": 6.0})

    assert result["new_count"] == 0
    assert result["compared_against_snapshot"] is None
    assert result["eligible_count"] == 1


def test_a_pair_absent_before_is_new(registry_db):
    computed.new_since_previous(registry_db, 100, {"a|b|1|2": 6.0})

    result = computed.new_since_previous(registry_db, 101,
                                         {"a|b|1|2": 6.0, "c|d|3|4": 7.0})

    assert result["new_keys"] == ["c|d|3|4"]
    assert result["compared_against_snapshot"] == 100


def test_a_pair_present_before_is_not_new(registry_db):
    computed.new_since_previous(registry_db, 100, {"a|b|1|2": 6.0})

    result = computed.new_since_previous(registry_db, 101, {"a|b|1|2": 6.5})

    assert result["new_count"] == 0, "a gap that changed is not a new pair"


def test_a_pair_that_leaves_and_returns_is_new_again(registry_db):
    computed.new_since_previous(registry_db, 100, {"a|b|1|2": 6.0})
    computed.new_since_previous(registry_db, 101, {})          # gone
    result = computed.new_since_previous(registry_db, 102, {"a|b|1|2": 6.0})

    assert result["new_keys"] == ["a|b|1|2"], (
        "it was absent at the previous recording, so its return is news"
    )


def test_a_snapshot_with_nothing_eligible_is_still_recorded(registry_db):
    """The distinction that makes the previous test work.

    "Nothing qualified" and "never examined" must not look the same: if the
    empty snapshot left no trace, the next comparison would reach past it to
    snapshot 100, find the pair already present, and report no news — hiding
    a pair that genuinely came back.
    """
    computed.new_since_previous(registry_db, 100, {"a|b|1|2": 6.0})
    computed.new_since_previous(registry_db, 101, {})

    assert db.get_previous_recorded_snapshot(registry_db, 102) == 101
    assert db.get_eligible_keys(registry_db, 101) == set()


def test_asking_twice_gives_the_same_answer(registry_db):
    """Idempotent. A retried request — a flaky phone, a double tap — must not
    report a pair as new once and then swallow it."""
    computed.new_since_previous(registry_db, 100, {"a|b|1|2": 6.0})

    first = computed.new_since_previous(registry_db, 101, {"c|d|3|4": 7.0})
    second = computed.new_since_previous(registry_db, 101, {"c|d|3|4": 7.0})

    assert first["new_keys"] == second["new_keys"] == ["c|d|3|4"]


def test_looking_without_recording_does_not_advance_the_comparison(registry_db):
    computed.new_since_previous(registry_db, 100, {"a|b|1|2": 6.0})

    peek = computed.new_since_previous(registry_db, 101, {"c|d|3|4": 7.0},
                                       record=False)
    after = computed.new_since_previous(registry_db, 102, {"c|d|3|4": 7.0})

    assert peek["new_keys"] == ["c|d|3|4"]
    assert peek["recorded"] is False
    assert after["compared_against_snapshot"] == 100, (
        "the unrecorded look must leave the comparison point where it was"
    )
    assert after["new_keys"] == ["c|d|3|4"]


def test_recording_the_same_snapshot_twice_does_not_duplicate_rows(registry_db):
    computed.new_since_previous(registry_db, 100, {"a|b|1|2": 6.0})
    computed.new_since_previous(registry_db, 100, {"a|b|1|2": 6.0})

    conn = sqlite3.connect(registry_db)
    count = conn.execute(
        "SELECT COUNT(*) FROM mc_eligible_keys WHERE snapshot_id = 100"
    ).fetchone()[0]
    conn.close()

    assert count == 1


def test_the_registry_survives_a_restart(registry_db):
    """The whole point of the table over st.session_state: the answer is a
    property of the record, not of a browser tab or a process."""
    computed.new_since_previous(registry_db, 100, {"a|b|1|2": 6.0})

    # Nothing is held in memory between these calls — a fresh read of the
    # file is exactly what a restarted server would do.
    assert db.get_eligible_keys(registry_db, 100) == {"a|b|1|2"}


def test_the_empty_marker_is_never_returned_as_a_pair(registry_db):
    """The marker row uses an empty pair_key, which no real key can be. If it
    leaked into the set it would show as a phantom eligible pair."""
    computed.new_since_previous(registry_db, 100, {})

    assert db.get_eligible_keys(registry_db, 100) == set()


def test_band_classification_counts_both_bands():
    sweep = _sweep(
        ("2026-09-11", "2026-10-02", 7700, 7750, 6.0),
        ("2026-09-11", "2026-10-02", 7600, 7650, 4.5),
        ("2026-09-11", "2026-10-02", 7500, 7550, 1.0),
    )

    bands = computed.classify(sweep)

    assert bands["eligible"] == 1
    assert bands["approaching"] == 1
    assert bands["total"] == 3
