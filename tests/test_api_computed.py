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


# ─────────────────────────────────────────────────────────────────────────────
# BUG-040 — the verb has to mean what it says
#
# The endpoint had NO test at this layer before. Everything above exercises
# `computed.new_since_previous` directly, where `record` is an explicit
# argument and its default never applies. That is exactly the gap that let a
# GET default to writing: the write was well tested and the route was not.
# ─────────────────────────────────────────────────────────────────────────────

def _registry_rows(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM mc_eligible_keys").fetchone()[0]
    finally:
        conn.close()


@pytest.fixture
def scan_client(temp_db):
    """A client over a database with one COMPLETE snapshot and a real chain."""
    from fastapi.testclient import TestClient

    from api.app import create_app
    from test_db import _gex_seed

    _gex_seed(temp_db)
    return TestClient(create_app(db_path=temp_db)), temp_db


def test_reading_the_new_flag_writes_nothing(scan_client):
    """THE DEFECT, at the layer that had it.

    Not "returns the right pairs" — that is covered above and was never wrong.
    The claim here is about side effects: a GET must leave the registry
    exactly as it found it. It did not, because `record` defaulted to True,
    and I advanced a previously empty registry to snapshot 6387 on 2026-09-05
    by calling this endpoint to look at its response shape.
    """
    client, db_path = scan_client
    before = _registry_rows(db_path)

    reply = client.get("/mission/new")

    assert reply.status_code == 200, reply.text
    assert reply.json()["recorded"] is False
    assert _registry_rows(db_path) == before, (
        "a GET advanced the comparison point"
    )


def test_reading_it_ten_times_still_writes_nothing(scan_client):
    """Safe AND repeatable, which is the half that bites under M6.

    One clean GET would pass even if the route wrote on a retry path.
    TanStack Query retries failed GETs and refetches on window focus, so the
    real access pattern is dozens of calls nobody made on purpose.
    """
    client, db_path = scan_client
    before = _registry_rows(db_path)

    for _ in range(10):
        assert client.get("/mission/new").status_code == 200

    assert _registry_rows(db_path) == before


def test_there_is_no_query_parameter_that_makes_the_get_write(scan_client):
    """The split has to remove the capability, not hide the default.

    Flipping `record`'s default to False would pass both tests above while
    leaving `?record=true` reachable — one query string from the same bug, and
    a URL is the easiest thing in the world to copy from an old note. FastAPI
    ignores unknown query parameters, so this asserts on the REGISTRY rather
    than on a 4xx.
    """
    client, db_path = scan_client
    before = _registry_rows(db_path)

    client.get("/mission/new", params={"record": "true"})

    assert _registry_rows(db_path) == before, (
        "?record=true still writes; the default moved but the capability stayed"
    )
    spec = client.get("/openapi.json").json()
    names = {p["name"]
             for p in spec["paths"]["/mission/new"]["get"].get("parameters", [])}
    assert "record" not in names, (
        "the GET still advertises a `record` parameter"
    )


def test_recording_is_reachable_and_does_advance_the_point(scan_client):
    """The other half. Splitting the verbs is only correct if the write is
    still callable — a comparison point that can never move reports every pair
    as new forever, which is the browser-tab behaviour M4.3 replaced."""
    client, db_path = scan_client

    reply = client.post("/mission/new/record")

    assert reply.status_code == 200, reply.text
    assert reply.json()["recorded"] is True
    assert _registry_rows(db_path) > 0, "the write endpoint recorded nothing"


def test_the_write_is_not_reachable_by_GET(scan_client):
    """A POST-only route. If the recorder answered GET as well, every rule
    above would be one URL away from being undone."""
    client, _ = scan_client

    assert client.get("/mission/new/record").status_code == 405


# ─────────────────────────────────────────────────────────────────────────────
# The cards, and the one-definition rule that made serving them worth doing
# ─────────────────────────────────────────────────────────────────────────────

def test_the_page_and_the_server_call_the_same_card_builder():
    """The whole reason this was extracted rather than copied.

    M6's governing rule is that no formula gets a second home. The cheap way
    to serve these cards was to reimplement Phase B in api/ — it would have
    passed every behavioural test on both sides for as long as the two copies
    happened to agree, and diverged the first time one was edited.

    Checked at the SOURCE, because no output can show it: two identical
    implementations produce identical cards right up until they do not.
    """
    from pathlib import Path

    import services.mission_control as mc

    source = Path(mc.__file__).read_text(encoding="utf-8")

    assert "computed.approaching_panel" in source, (
        "services/ no longer calls down into the shared builder — if the cards "
        "are being built in services/ again, the server has a second copy"
    )
    assert "def _build_cards(" not in source, (
        "the card builder is back in services/, so there are two of them"
    )
    assert "np.polyfit" not in source, (
        "the ETA projection is back in services/; it belongs in one place"
    )


def test_the_cards_endpoint_answers_with_both_grids(scan_client):
    """Shape, not arithmetic — the arithmetic is pinned above and unchanged by
    the move. What is new is that these reach a front end at all."""
    client, _ = scan_client

    body = client.get("/mission/cards").json()

    assert set(body) >= {"snapshot_id", "spot", "approaching_cards",
                         "likely_next", "n_approaching"}
    assert isinstance(body["approaching_cards"], list)
    assert isinstance(body["likely_next"], list)


def test_the_cards_endpoint_reads_the_database_it_was_given(scan_client):
    """`candidate_signals` lost its config.DB_PATH default in the move, and
    this is why. With a default, a forgotten argument answers from the
    production record — silently, and only in production, where no test
    database is in the way to make it obvious."""
    import inspect

    from api import computed

    sig = inspect.signature(computed.candidate_signals)
    assert sig.parameters["db_path"].default is inspect.Parameter.empty, (
        "db_path has a default again; the server can now answer one "
        "database's question with another's data"
    )

    client, db_path = scan_client
    assert client.get("/mission/cards").status_code == 200
