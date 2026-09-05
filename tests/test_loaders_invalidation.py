"""ENH-011 — the memoised reads expire on a new snapshot, and on nothing else.

WHY THIS FILE EXISTS SEPARATELY. tests/app_loader.py strips the cache
decorators before exec'ing the pipeline, which is right for the golden tests
— they are about the arithmetic, and a cache would only make them flaky. But
it means nothing in the suite exercises the caching itself. The behaviour
changed on 2026-09-05 from "expires after 55 seconds" to "expires when the
collector writes a new snapshot", and the failure mode of getting that wrong
is not a crash: it is a dashboard quietly showing yesterday's history.

NO DATABASE IS TOUCHED. Every test below replaces dataaccess.queries with a
counter, so what is being measured is whether the wrapper called through —
which is the whole question — and not what any query returns.
"""
from __future__ import annotations

import pandas as pd
import pytest

from dataaccess import queries
from services import loaders


@pytest.fixture(autouse=True)
def _fresh_cache():
    """Each test starts with nothing remembered and no snapshot claimed.

    Without this the tests would pass or fail depending on the order they ran
    in, since the cache and the generation marker are both process-wide.
    """
    loaders._generation.clear()
    for memo in loaders._SNAPSHOT_SCOPED:
        memo.clear()
    loaders._load_prior_close.clear()
    yield
    loaders._generation.clear()


@pytest.fixture
def counting_atm_hist(monkeypatch):
    """A stand-in for the ATM history read that reports how often it ran."""
    calls = {"n": 0}

    def fake(db_path, expiry, days):
        calls["n"] += 1
        return pd.DataFrame({"call": [calls["n"]]})

    monkeypatch.setattr(queries, "load_atm_hist", fake)
    return calls


# ─────────────────────────────────────────────────────────────────────────────
# The memo holds for as long as the snapshot does
# ─────────────────────────────────────────────────────────────────────────────

def test_a_repeated_read_within_one_snapshot_does_not_query_again(counting_atm_hist):
    loaders.invalidate_on_new_snapshot(1)

    loaders._load_atm_hist("2026-09-11", 90)
    loaders._load_atm_hist("2026-09-11", 90)

    assert counting_atm_hist["n"] == 1


def test_the_memo_does_not_expire_on_time(counting_atm_hist, monkeypatch):
    """The point of ENH-011. There is no TTL left to lapse, so a click after a
    long idle costs nothing extra — measured at 0.65s wasted before this
    change (docs/m5_measurement.md).

    Time is not mocked here because nothing reads a clock any more; that is
    exactly what is being asserted. A wrapper that still carried a ttl would
    need the clock advanced to fail this, so instead the guard is below:
    test_no_wrapper_carries_a_ttl.
    """
    loaders.invalidate_on_new_snapshot(1)
    loaders._load_atm_hist("2026-09-11", 90)
    loaders._load_atm_hist("2026-09-11", 90)

    assert counting_atm_hist["n"] == 1


def test_no_wrapper_carries_a_ttl():
    """A TTL reintroduced by a future edit puts the clock back in charge.

    Read from the source rather than the objects because Streamlit does not
    expose the ttl of a cached function; the decorator line is where the
    mistake would be made, so the decorator line is what is checked.
    """
    source = (loaders.__file__)
    with open(source, encoding="utf-8") as fh:
        text = fh.read()

    offenders = [line.strip() for line in text.splitlines()
                 if "ttl=" in line and not line.strip().startswith("#")]

    assert offenders == [], (
        "services/loaders.py invalidates on the snapshot, not the clock "
        f"(ENH-011). Remove the ttl: {offenders}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# ...and is dropped when a new one lands
# ─────────────────────────────────────────────────────────────────────────────

def test_a_new_snapshot_drops_the_saved_results(counting_atm_hist):
    """The load-bearing one. _load_atm_hist does NOT take snapshot_id — the
    TTL used to be its only invalidation, so if this call stops clearing, it
    caches yesterday's history for the life of the process."""
    loaders.invalidate_on_new_snapshot(1)
    first = loaders._load_atm_hist("2026-09-11", 90)

    loaders.invalidate_on_new_snapshot(2)
    second = loaders._load_atm_hist("2026-09-11", 90)

    assert counting_atm_hist["n"] == 2
    assert first["call"][0] == 1
    assert second["call"][0] == 2, "the second read must see the newer data"


def test_the_same_snapshot_twice_clears_nothing(counting_atm_hist):
    """Called once per script run — every tab click, every widget change.
    Clearing on each would make the cache useless in exactly the case it
    exists for."""
    assert loaders.invalidate_on_new_snapshot(7) is True
    assert loaders.invalidate_on_new_snapshot(7) is False

    loaders._load_atm_hist("2026-09-11", 90)
    loaders.invalidate_on_new_snapshot(7)
    loaders._load_atm_hist("2026-09-11", 90)

    assert counting_atm_hist["n"] == 1


def test_finished_history_survives_a_new_snapshot(monkeypatch):
    """The deliberate exclusion. A prior session's close cannot change, and it
    already carries session_date in its key. Clearing it per snapshot would
    re-query immutable history more often than the 300s TTL it replaced —
    making the change a regression for that read rather than an improvement.
    """
    calls = {"n": 0}

    def fake(db_path, session_date):
        calls["n"] += 1
        return 7700.0

    monkeypatch.setattr(queries, "load_prior_close", fake)

    loaders.invalidate_on_new_snapshot(1)
    loaders._load_prior_close("2026-09-04")
    loaders.invalidate_on_new_snapshot(2)
    loaders._load_prior_close("2026-09-04")

    assert calls["n"] == 1, "an immutable past session was re-queried"


def test_every_snapshot_scoped_wrapper_can_actually_be_cleared():
    """A plain function in the tuple — one whose decorator was dropped in an
    edit — would raise AttributeError on the next new snapshot, taking the
    whole page down rather than merely running slowly."""
    for memo in loaders._SNAPSHOT_SCOPED:
        assert hasattr(memo, "clear"), f"{memo!r} is not a cached function"
