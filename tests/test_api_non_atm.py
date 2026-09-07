"""DEBT-041 — the non-ATM opportunities panel, served.

WHAT THIS MILESTONE IS FOR, and therefore what these tests have to prove.
The point of moving a card grid below the layer boundary is not that the
server gains an endpoint. It is that the page and the server stop being able
to disagree — one definition, two callers. So the load-bearing test here is
`test_the_page_and_the_server_return_the_same_cards`: everything else could
pass while the two quietly drifted, and that is precisely the failure the
whole exercise exists to prevent.

The rest guard the seam the move introduced. Serving this panel meant the
server learning a SECOND process-level fact — where the JSON sidecars live —
and two things about that are worth pinning rather than assuming:

  * the registry is READ and never written. `api/` is read-only apart from one
    documented exception and this is not a second one.
  * the state directory is honoured. A route that ignored it and reached for
    `config.STATE_DIR` would pass every functional test on a developer's
    machine while reading — and, if a write ever crept in, overwriting — the
    real ~700 KB registry.

The four-tier sort and the never-empty fallback are NOT re-tested here. Twelve
tests in tests/test_mc_pipeline_golden.py already pin them and, since the
move, they exercise this exact body through the page's wrapper. Asserting the
same things a second time would not make them truer; it would make them two
places to update.
"""
from __future__ import annotations

import json

import pandas as pd
import pytest
from conftest import (
    MC_BACK_EXPIRY,
    MC_CALL_STRIKE,
    MC_FRONT_EXPIRY,
    MC_PUT_STRIKE,
    make_transform_history,
)
from fastapi.testclient import TestClient

import config
from api import computed
from api.app import create_app

KEY = f"{MC_FRONT_EXPIRY}|{MC_BACK_EXPIRY}|{int(MC_PUT_STRIKE)}|{int(MC_CALL_STRIKE)}"


def _entry(*, last_seen: str, max_gap: float = 6.0, hits: int = 3) -> dict:
    """One registry row, shaped as services/mission_control.py writes them."""
    return {
        "front_raw": MC_FRONT_EXPIRY, "back_raw": MC_BACK_EXPIRY,
        "put_strike": int(MC_PUT_STRIKE), "call_strike": int(MC_CALL_STRIKE),
        "iv_ratio": 1.02, "first_seen": last_seen, "last_seen": last_seen,
        "last_gap": max_gap, "max_gap": max_gap, "hit_count": hits,
    }


@pytest.fixture
def served(tmp_path, temp_db):
    """A server bound to a throwaway database AND a throwaway state directory.

    The second binding is the one worth noticing. Before DEBT-041 an api/ test
    needed only `db_path`; this panel reads a sidecar file, so a test that
    forgot the state directory would be reading the developer's real registry
    — the same hazard `load_pipeline` asserts against on the page side.

    Returns (client, state_dir, write_registry).
    """
    make_transform_history(temp_db, [6.0, 6.5], interval_minutes=5)
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    def write_registry(registry: dict) -> None:
        (state_dir / "eligible_history.json").write_text(
            json.dumps(registry), encoding="utf-8")

    client = TestClient(create_app(db_path=temp_db, state_dir=str(state_dir)))
    return client, state_dir, write_registry


# ─────────────────────────────────────────────────────────────────────────────
# The reason the move happened
# ─────────────────────────────────────────────────────────────────────────────

def test_the_page_and_the_server_return_the_same_cards(tmp_path, temp_db,
                                                       monkeypatch):
    """ONE definition, two callers. This is what DEBT-041 was opened for.

    Both sides are called with the same registry, the same sweep and the same
    window, and their answers are compared field by field. If the page's
    wrapper ever grows a rule of its own — a different cap, an extra sort, a
    label built differently — this fails, and it is the only test that would.

    The page returns a positional triple and the server a named payload; that
    difference is deliberate (JSON has no tuples) and is unpacked here rather
    than papered over, so the SHAPES stay free to differ while the CONTENT
    cannot.
    """
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", temp_db)
    make_transform_history(temp_db, [6.0], interval_minutes=5)

    from services import mission_control

    registry = {KEY: _entry(last_seen="2026-08-01 14:00:00")}
    args = (
        pd.DataFrame(),                     # nothing live right now
        registry,
        {MC_FRONT_EXPIRY: 7, MC_BACK_EXPIRY: 21},
        "2000-01-01",                       # a window wide enough to include it
        "2026-08-01 15:00:00",
    )

    page_cards, page_in_window, page_fallback = \
        mission_control._build_non_atm_panel(*args)
    served = computed.non_atm_panel(*args, db_path=temp_db)

    assert page_cards == served["cards"]
    assert page_in_window == served["in_window_total"]
    assert page_fallback == served["fallback_used"]
    assert page_cards, "both sides agreed on nothing at all — test is vacuous"


# ─────────────────────────────────────────────────────────────────────────────
# The endpoint
# ─────────────────────────────────────────────────────────────────────────────

def test_the_endpoint_serves_a_card_built_from_the_registry(served):
    """The grid a front end could not previously get at all.

    Note what makes this card exist: nothing in the current sweep. It is in
    the registry and nowhere else, which is exactly the half of Mission
    Control that /mission/cards cannot reach.
    """
    client, _, write_registry = served
    write_registry({KEY: _entry(last_seen="2026-08-01 14:00:00")})

    payload = client.get("/mission/non-atm?lookback=20").json()

    assert payload["registry_entries"] == 1
    assert len(payload["cards"]) == 1
    card = payload["cards"][0]
    assert card["put_strike"] == int(MC_PUT_STRIKE)
    assert card["call_strike"] == int(MC_CALL_STRIKE)
    assert card["hit_count"] == 3


def test_an_empty_registry_gives_an_empty_panel_rather_than_an_error(served):
    """Cold start is a real state, not a fault — nothing has ever crossed the
    threshold on a fresh install, and the panel says so by being empty."""
    client, _, write_registry = served
    write_registry({})

    payload = client.get("/mission/non-atm").json()

    assert payload["cards"] == []
    assert payload["in_window_total"] == 0
    assert payload["registry_entries"] == 0


def test_a_missing_registry_file_is_not_a_server_error(served):
    """No file at all is the same state as an empty one, and must read that
    way: `state/store.read_json` returns {} rather than raising."""
    client, _, _ = served

    response = client.get("/mission/non-atm")

    assert response.status_code == 200
    assert response.json()["cards"] == []


def test_the_response_reports_how_much_registry_it_answered_from(served):
    """`registry_entries` is in the payload on purpose.

    The registry only advances while the Streamlit page runs, so a caller
    needs some way to see that it was answered from a nearly-empty one rather
    than inferring "no opportunities" from a short list.
    """
    client, _, write_registry = served
    write_registry({
        KEY: _entry(last_seen="2026-08-01 14:00:00"),
        f"{MC_FRONT_EXPIRY}|{MC_BACK_EXPIRY}|5800|6200":
            {**_entry(last_seen="2026-08-01 13:00:00"),
             "put_strike": 5800, "call_strike": 6200},
    })

    assert client.get("/mission/non-atm").json()["registry_entries"] == 2


# ─────────────────────────────────────────────────────────────────────────────
# The seam the move introduced
# ─────────────────────────────────────────────────────────────────────────────

def test_reading_the_panel_never_writes_the_registry(served):
    """BUG-040's lesson, applied before it can happen again.

    A GET that wrote would be retried and refetched by TanStack Query on
    window focus, advancing state with no user action behind it. Checked by
    byte comparison rather than by reading the code, because "it does not
    write" is exactly the kind of claim that stays true until someone adds a
    convenience.
    """
    client, state_dir, write_registry = served
    write_registry({KEY: _entry(last_seen="2026-08-01 14:00:00")})
    path = state_dir / "eligible_history.json"
    before = path.read_bytes()

    client.get("/mission/non-atm?lookback=20")
    client.get("/mission/non-atm?lookback=20")

    assert path.read_bytes() == before
    assert sorted(p.name for p in state_dir.iterdir()) == \
        ["eligible_history.json"], "the panel created a file it should not have"


def test_the_bound_state_directory_is_the_one_actually_read(tmp_path, temp_db):
    """Not config.STATE_DIR. The whole reason create_app takes it.

    Two servers over the same database and different state directories must
    disagree, and only about this. If the route reached for config instead,
    both would answer identically — and identically wrong on any machine
    where the real registry exists.
    """
    make_transform_history(temp_db, [6.0], interval_minutes=5)

    populated, empty = tmp_path / "a", tmp_path / "b"
    for d in (populated, empty):
        d.mkdir()
    (populated / "eligible_history.json").write_text(
        json.dumps({KEY: _entry(last_seen="2026-08-01 14:00:00")}),
        encoding="utf-8")

    with_cards = TestClient(create_app(db_path=temp_db,
                                       state_dir=str(populated)))
    without = TestClient(create_app(db_path=temp_db, state_dir=str(empty)))

    assert with_cards.get("/mission/non-atm?lookback=20").json()["cards"]
    assert without.get("/mission/non-atm?lookback=20").json()["cards"] == []


def test_the_lookback_window_counts_sessions_and_is_bounded(served):
    """BUG-035: the window is SESSIONS ON RECORD, never calendar days.

    The bound is checked too — an unbounded value would let one request sweep
    the whole record, the read pattern DATABASE.md documents as absent.
    """
    client, _, _ = served

    assert client.get("/mission/non-atm?lookback=0").status_code == 422
    assert client.get("/mission/non-atm?lookback=100000").status_code == 422
    assert client.get("/mission/non-atm?lookback=20").status_code == 200


def test_an_entry_older_than_the_window_is_flagged_rather_than_hidden(served):
    """The never-empty fallback, at the endpoint rather than in the unit.

    Pinned here because the FLAG is what a front end has to render
    differently, and a payload that dropped `outside_lookback` on the way to
    JSON would show stale cards as current with nothing to distinguish them.
    """
    client, _, write_registry = served
    write_registry({KEY: _entry(last_seen="2020-01-01 14:00:00")})

    payload = client.get("/mission/non-atm?lookback=1").json()

    assert payload["in_window_total"] == 0, "the entry is far outside the window"
    assert payload["fallback_used"] == 1
    assert payload["cards"][0]["outside_lookback"] is True


# ─────────────────────────────────────────────────────────────────────────────
# The cache key
# ─────────────────────────────────────────────────────────────────────────────

def test_a_registry_rewrite_invalidates_the_cached_panel(served):
    """The half of the cache key the snapshot cannot supply.

    Measured on the live record 2026-09-06, this endpoint cost 1.67s on every
    call before it was memoised, so it has to be cached. But the obvious key —
    the snapshot, which is what every other route here uses — is wrong for
    this one: the registry is rewritten by the Streamlit page, a DIFFERENT
    process, so it changes while the snapshot has not.

    Without the fingerprint in the key this test fails by returning the FIRST
    answer forever, which is the worst shape of stale: a panel that looks
    current, is internally consistent, and silently stops reflecting anything
    the page has recorded since the server started.
    """
    client, _, write_registry = served
    write_registry({})
    assert client.get("/mission/non-atm?lookback=20").json()["cards"] == []

    write_registry({KEY: _entry(last_seen="2026-08-01 14:00:00")})

    payload = client.get("/mission/non-atm?lookback=20").json()
    assert len(payload["cards"]) == 1, (
        "the cached panel survived a registry rewrite — the fingerprint is "
        "missing from the cache key"
    )
    assert payload["registry_entries"] == 1


def test_an_unchanged_registry_is_served_from_the_cache(served):
    """The other direction: the key must not be so specific it never hits.

    A fingerprint that included, say, an access time would invalidate on every
    read and quietly restore the 1.67s — with every test above still passing,
    because they only ever check the answer.
    """
    client, _, write_registry = served
    write_registry({KEY: _entry(last_seen="2026-08-01 14:00:00")})

    client.get("/mission/non-atm?lookback=20")
    before = client.app.state.cache.stats()["hits"]
    client.get("/mission/non-atm?lookback=20")

    assert client.app.state.cache.stats()["hits"] > before


def test_the_registry_count_describes_the_panel_that_was_built(served):
    """`registry_entries` travels inside the cached value, not beside it.

    Counted outside, it would be a fresh read of the file taken at serve time
    while the cards came from the cache — two different registries reported as
    one answer. Exactly the BUG-029 shape: two days compared under one label.
    """
    client, _, write_registry = served
    write_registry({KEY: _entry(last_seen="2026-08-01 14:00:00")})
    first = client.get("/mission/non-atm?lookback=20").json()

    write_registry({})
    second = client.get("/mission/non-atm?lookback=20").json()

    assert (first["registry_entries"], len(first["cards"])) == (1, 1)
    assert (second["registry_entries"], len(second["cards"])) == (0, 0)


def test_each_card_carries_the_key_mission_new_answers_in(served):
    """THE JOIN THE REACT SCANNER NEEDS, MADE IN PYTHON.

    `/mission/new` returns `new_keys` as "front|back|put|call" strings. A card
    has to know whether it is one of them to show a NEW badge. Rebuilding that
    string in TypeScript would be a formula in the new language, and a
    mismatched one fails SILENTLY — nothing errors, nothing is ever new.

    So the key travels with the card, and this asserts it is the SAME key
    `pair_key` makes, not merely a plausible-looking one.
    """
    client, _state_dir, write_registry = served
    write_registry({KEY: _entry(last_seen="2026-09-04T20:01:00")})

    cards = client.get("/mission/non-atm?lookback=20").json()["cards"]

    assert cards, "the fixture registry has an entry, so a card is expected"
    for card in cards:
        assert card["key"] == computed.pair_key(
            card["front_raw"], card["back_raw"],
            card["put_strike"], card["call_strike"])
