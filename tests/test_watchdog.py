"""scripts/watchdog.py — the alarm that does not need the dashboard open.

WHAT THESE TESTS ARE REALLY FOR. The easy half of a watchdog is noticing an
outage; the hard half is not crying wolf, because an alarm that fires when
nothing is wrong gets muted, and a muted alarm is worse than none — you
believe you are covered. So most of this file is about the QUIET cases:
weekends, the minutes after the opening bell, the moment before each poll
lands, and the twelfth email about the same dead collector.

Every test drives `check()` with an explicit `now`, against a temporary
database. `config.DB_PATH` is monkeypatched and the first test asserts the
patch took, because a leak would mean these tests reading production while
reporting on a fixture.

NOTHING HERE SENDS ANYTHING. `notify_desktop` and `notify_email` are never
called; `should_alert` — the decision — is tested directly. Testing that an
email leaves the machine is not this suite's job and is what `--test-alert`
is for.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import config
import db

REAL_DB_PATH = config.DB_PATH

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import watchdog  # noqa: E402  — requires the sys.path line above

pytestmark = pytest.mark.integration

# Wednesday 15 July 2026 is an ordinary trading day.
# ET is UTC-4 in July, so 16:00 UTC = 12:00 ET = MIDDAY (300s cadence),
# and 13:45 UTC = 09:45 ET = OPEN (60s cadence).
MIDDAY = datetime(2026, 7, 15, 16, 0, tzinfo=UTC)
OPEN_SESSION = datetime(2026, 7, 15, 13, 45, tzinfo=UTC)
JUST_OPENED = datetime(2026, 7, 15, 13, 32, tzinfo=UTC)     # 09:32 ET
SATURDAY = datetime(2026, 7, 18, 16, 0, tzinfo=UTC)
OVERNIGHT = datetime(2026, 7, 15, 3, 0, tzinfo=UTC)         # 23:00 ET Tuesday


def _snapshot_at(path: str, when_utc: datetime) -> None:
    """One COMPLETE snapshot with the given timestamp."""
    sid = db.create_snapshot(path, when_utc.strftime("%Y-%m-%d %H:%M:%S"),
                             "OPEN", 60, underlying_price=6000.0,
                             underlying_bid=6000.0, underlying_ask=6000.0,
                             vix_value=15.0)
    db.finalize_snapshot(path, sid, "COMPLETE", 10, 2, 500)


@pytest.fixture
def wd_db(temp_db, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", temp_db)
    monkeypatch.setattr(watchdog.config, "DB_PATH", temp_db)
    # The token line is a bonus fact, not what these tests are about; pin it so
    # a real token expiring on a Tuesday cannot turn these red.
    monkeypatch.setattr(watchdog, "_token_note", lambda: "Token has 5.0 days left.")
    return temp_db


def test_the_test_database_is_not_the_production_one(wd_db):
    """First on purpose — see the same check in test_prune_script.py."""
    assert Path(wd_db).resolve() != Path(REAL_DB_PATH).resolve()
    assert wd_db == watchdog.config.DB_PATH


# ─────────────────────────────────────────────────────────────────────────────
# Cry-wolf case 1 — the market is shut
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("when,label", [(SATURDAY, "weekend"), (OVERNIGHT, "overnight")])
def test_a_shut_market_is_never_an_alarm(wd_db, when, label):
    """The collector is idle BY DESIGN out of hours. If this fails, the
    watchdog emails every night at midnight and is muted within a week."""
    _snapshot_at(wd_db, when - timedelta(days=3))     # ancient, deliberately
    result = watchdog.check(when)
    assert result["ok"], label
    assert "closed" in result["headline"].lower()


def test_a_holiday_is_treated_as_shut(wd_db, monkeypatch):
    """A weekday the market is closed — the case a weekend-only check misses."""
    monkeypatch.setattr(watchdog.config, "MARKET_HOLIDAYS", {"2026-07-15"})
    _snapshot_at(wd_db, MIDDAY - timedelta(days=2))
    assert watchdog.check(MIDDAY)["ok"]


# ─────────────────────────────────────────────────────────────────────────────
# Cry-wolf case 2 — the minutes after the opening bell
# ─────────────────────────────────────────────────────────────────────────────

def test_two_minutes_after_the_bell_is_not_an_alarm(wd_db):
    """At 09:32 the newest price is legitimately yesterday's 16:00 close. An
    age check without a grace period alarms EVERY trading morning."""
    _snapshot_at(wd_db, JUST_OPENED - timedelta(hours=17))
    result = watchdog.check(JUST_OPENED)
    assert result["ok"]
    assert "grace" in result["detail"].lower()


def test_but_it_does_alarm_once_the_grace_period_is_over(wd_db):
    """The other side of the same boundary — without this, the test above is
    satisfied by a watchdog that never alarms in the morning at all."""
    _snapshot_at(wd_db, OPEN_SESSION - timedelta(hours=17))
    assert not watchdog.check(OPEN_SESSION)["ok"]


# ─────────────────────────────────────────────────────────────────────────────
# Cry-wolf case 3 — the moment before each poll lands
# ─────────────────────────────────────────────────────────────────────────────

def test_data_as_old_as_the_interval_is_normal_not_late(wd_db):
    """At a 300s cadence the age reaches 300s immediately before every new
    price. That is the cadence working."""
    _snapshot_at(wd_db, MIDDAY - timedelta(seconds=300))
    assert watchdog.check(MIDDAY)["ok"]


def test_one_missed_cycle_is_tolerated_and_three_are_not(wd_db):
    _snapshot_at(wd_db, MIDDAY - timedelta(seconds=600))     # 2x — still under 2.5x
    assert watchdog.check(MIDDAY)["ok"]


def test_a_long_silence_during_market_hours_is_an_alarm(wd_db):
    _snapshot_at(wd_db, MIDDAY - timedelta(seconds=3600))
    result = watchdog.check(MIDDAY)
    assert not result["ok"]
    assert result["severity"] == "alarm"
    assert "cannot be recovered" in result["detail"]


def test_the_threshold_is_tighter_in_the_first_half_hour(wd_db):
    """Four minutes old is fine at midday and late at the open, because the
    collector polls every 60s then. The two answers come from the SAME
    function the collector uses (core/session.py) — this is what stops the
    header, the watchdog and the collector drifting apart."""
    _snapshot_at(wd_db, OPEN_SESSION - timedelta(seconds=240))
    assert not watchdog.check(OPEN_SESSION)["ok"]

    # Same age, midday: healthy.
    _snapshot_at(wd_db, MIDDAY - timedelta(seconds=240))
    assert watchdog.check(MIDDAY)["ok"]


# ─────────────────────────────────────────────────────────────────────────────
# The things that must never read as healthy
# ─────────────────────────────────────────────────────────────────────────────

def test_an_unreadable_database_is_an_alarm(wd_db, monkeypatch):
    """Silence and 'all well' must not look the same. The database is the one
    thing this script depends on."""
    def _boom(*_a, **_k):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(watchdog.db, "get_latest_complete_snapshot", _boom)
    result = watchdog.check(MIDDAY)
    assert not result["ok"] and result["severity"] == "alarm"


def test_no_snapshots_at_all_is_an_alarm(wd_db):
    assert not watchdog.check(MIDDAY)["ok"]


def test_a_price_timestamped_in_the_future_is_an_alarm(wd_db):
    """Found by probing during M3.4: this reported 'collecting normally,
    newest price -2001584s old'. Every check here is a comparison against a
    clock, so a wrong clock makes the reassuring answers meaningless too."""
    _snapshot_at(wd_db, MIDDAY + timedelta(hours=2))
    result = watchdog.check(MIDDAY)
    assert not result["ok"]
    assert "FUTURE" in result["headline"]


# ─────────────────────────────────────────────────────────────────────────────
# Cry-wolf case 4 — not saying it twelve times an hour
# ─────────────────────────────────────────────────────────────────────────────

ALARM = {"ok": False, "severity": "alarm", "headline": "x", "detail": "y"}
FINE = {"ok": True, "severity": "ok", "headline": "x", "detail": "y"}
NOW = MIDDAY


def test_the_first_alarm_is_sent():
    send, kind = watchdog.should_alert(ALARM, NOW, {})
    assert send and kind == "new"


def test_the_same_alarm_five_minutes_later_is_not():
    state = {"alarming": True, "last_alert_utc": (NOW - timedelta(minutes=5)).isoformat()}
    send, _ = watchdog.should_alert(ALARM, NOW, state)
    assert not send


def test_but_it_is_repeated_after_the_re_alert_window():
    """An outage that lasts all day must not go quiet after one message."""
    state = {"alarming": True,
             "last_alert_utc": (NOW - timedelta(
                 minutes=config.WATCHDOG_REALERT_MINUTES + 1)).isoformat()}
    send, kind = watchdog.should_alert(ALARM, NOW, state)
    assert send and kind == "repeat"


def test_recovery_is_announced():
    """Once an alarm has fired, silence means either 'fixed' or 'the watchdog
    died too'. Only one is good news, so it says which."""
    send, kind = watchdog.should_alert(FINE, NOW, {"alarming": True})
    assert send and kind == "recovered"


def test_a_quiet_day_stays_quiet():
    send, _ = watchdog.should_alert(FINE, NOW, {"alarming": False})
    assert not send


# ─────────────────────────────────────────────────────────────────────────────
# The false all-clear
# ─────────────────────────────────────────────────────────────────────────────

NO_NEWS = {"ok": True, "severity": "ok", "headline": "Market closed",
           "detail": "", "informative": False}


def test_the_market_closing_does_not_announce_a_recovery():
    """THE BUG THIS FILE EXISTS TO PREVENT COMING BACK.

    Collector dead all afternoon, 16:00 arrives, the check flips to 'ok —
    market closed', and the watchdog emails 'prices are arriving again'. They
    are not. The market shut and the watchdog stopped being able to see.

    A false all-clear is the worst thing an alarm can say, because it is
    precisely the message that stops you looking. Recovery must require
    positively observing fresh data, not merely the absence of a complaint.
    """
    send, kind = watchdog.should_alert(NO_NEWS, NOW, {"alarming": True})
    assert not send, f"would have sent a {kind} alert on a market close"


def test_the_opening_grace_period_does_not_announce_a_recovery_either():
    """Same shape, different hour: broken overnight, 09:31 arrives, and the
    grace period answers 'ok' before any new price has actually landed."""
    send, _ = watchdog.should_alert(NO_NEWS, NOW, {"alarming": True})
    assert not send


def test_seeing_real_data_does_still_announce_a_recovery():
    """The other side of it. Without this, the two tests above are satisfied
    by a watchdog that never sends an all-clear at all — and then silence
    means both 'fixed' and 'dead', which is where we started."""
    send, kind = watchdog.should_alert(FINE, NOW, {"alarming": True})
    assert send and kind == "recovered"


@pytest.mark.parametrize("when", [SATURDAY, OVERNIGHT])
def test_a_shut_market_reports_itself_as_uninformative(wd_db, when):
    """Ties the flag to the real code path rather than to a hand-built dict —
    otherwise the tests above pass while check() never sets the flag."""
    _snapshot_at(wd_db, when - timedelta(days=3))
    assert watchdog.check(when)["informative"] is False


def test_a_normal_healthy_check_is_informative(wd_db):
    _snapshot_at(wd_db, MIDDAY - timedelta(seconds=60))
    assert watchdog.check(MIDDAY)["informative"] is True


def test_a_corrupt_state_file_does_not_stop_the_check(tmp_path, monkeypatch):
    """The note about what was already reported is a convenience. Losing it
    costs one duplicate alert; letting it raise costs the alarm entirely."""
    bad = tmp_path / "watchdog_state.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(watchdog, "STATE_PATH", bad)
    assert watchdog.load_state() == {}


# ─────────────────────────────────────────────────────────────────────────────
# BUG-029 — printing must never be the reason an alarm does not go out
#
# Found 2026-09-03 while rehearsing an outage. main() printed the headline
# BEFORE it reached the alerting block, and the headline starts with an emoji.
# On Windows a redirected stdout defaults to cp1252, which cannot encode it, so
# `python scripts/watchdog.py > out.txt` died at exit 1 with the check already
# complete and no alert sent — and the wreckage looked like the watchdog itself
# being broken, which is the most misleading way for an alarm to fail.
#
# The live alarm was never affected: register_watchdog_task.ps1 redirects
# nothing. The exposure was any log capture, supervisor, or human piping the
# output to read it.
# ─────────────────────────────────────────────────────────────────────────────

class _FrozenDatetime(datetime):
    """Pins main()'s clock so the 3-hour-old snapshot below lands in MIDDAY."""

    @classmethod
    def now(cls, tz=None):
        return MIDDAY


class _Cp1252Stream:
    """A stdout that behaves like a Windows pipe: ASCII only, and loud."""

    def __init__(self):
        self.written = []

    def write(self, text):
        text.encode("cp1252")       # raises UnicodeEncodeError on the icons
        self.written.append(text)
        return len(text)

    def flush(self):
        pass


class _DeadStream:
    """A stdout that cannot be written to at all, by any encoding."""

    def write(self, text):
        raise OSError("the pipe is gone")

    def flush(self):
        pass


def test_say_survives_a_stream_that_cannot_encode_the_icons(monkeypatch):
    """The direct reproduction. A bare print() here is what used to raise."""
    stream = _Cp1252Stream()
    monkeypatch.setattr(sys, "stdout", stream)
    watchdog._say("🚨 No prices for 3h 0m — collection has stopped")
    assert stream.written, "the message should still get through, degraded"
    assert "collection has stopped" in "".join(stream.written)


def test_say_survives_a_stream_that_is_gone_entirely(monkeypatch):
    """The fallback has a fallback. Output is a nicety; not raising is the
    contract, because the caller is about to send an alert."""
    monkeypatch.setattr(sys, "stdout", _DeadStream())
    watchdog._say("🚨 anything at all")      # must not raise


def test_the_alert_is_still_sent_when_the_headline_cannot_be_printed(
        wd_db, monkeypatch):
    """THE test for BUG-029. Everything above is about not crashing; this is
    about the alarm still arriving, which is the only thing that matters.

    Drives main() end to end with an unprintable stdout and a database whose
    newest price is hours old, and asserts both channels were called.
    """
    _snapshot_at(wd_db, MIDDAY - timedelta(hours=3))
    monkeypatch.setattr(watchdog, "STATE_PATH", Path(wd_db).parent / "wd_state.json")
    monkeypatch.setattr(watchdog, "datetime", _FrozenDatetime)

    sent = []
    monkeypatch.setattr(watchdog, "notify_desktop",
                        lambda t, b: sent.append(("desktop", t)) or True)
    monkeypatch.setattr(watchdog, "notify_email",
                        lambda t, b: sent.append(("email", t)) or True)
    monkeypatch.setattr(sys, "stdout", _Cp1252Stream())

    rc = watchdog.main([])

    assert rc == 1, "a stopped collector is exit 1, not a crash at exit 1"
    assert [c for c, _ in sent] == ["desktop", "email"], \
        "both channels must fire even though the headline could not be printed"


def test_configure_output_does_not_blow_up_on_an_odd_stream(monkeypatch):
    """It is called before anything is printed, so it must tolerate whatever
    stdout happens to be — including pytest's capture object."""
    monkeypatch.setattr(sys, "stdout", _DeadStream())
    watchdog._configure_output()     # must not raise
