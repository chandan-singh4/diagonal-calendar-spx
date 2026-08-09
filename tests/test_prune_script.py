"""
prune.py — the safety gates on the only script that deletes irreplaceable data.

WHAT THESE TESTS ARE FOR. db.plan_prune/execute_prune decide *which* rows go;
tests/test_retention.py covers that. This file covers everything standing
between a person at a keyboard and those functions running: the flag, the
backup guard, and the confirmation prompt. Those gates are the whole reason the
script is safe to have, and a gate nobody tests is a gate that quietly stops
latching.

Every test drives main() against a temporary database — config.DB_PATH is
monkeypatched, so nothing here can reach the real one. The assertion that the
patch took is itself a test below, because a leak would mean these tests
deleting production data while reporting success.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

import config
import db

# Captured before any fixture patches config, so the guard test below has a
# truthful "this is the production database" to compare against.
REAL_DB_PATH = config.DB_PATH

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import prune  # noqa: E402  — requires the sys.path line above

pytestmark = pytest.mark.integration

OLD_EXPIRY = "2026-01-15"
TODAY = "2026-08-09"


def _seed(path: str, expiry: str = OLD_EXPIRY, n: int = 5) -> None:
    sid = db.create_snapshot(path, "2026-01-10 15:00:00", "OPEN", 60,
                             underlying_price=6000.0, underlying_bid=6000.0,
                             underlying_ask=6000.0, vix_value=15.0)
    db.finalize_snapshot(path, sid, "COMPLETE", 10, 2, 500)
    db.insert_option_rows(path, [{
        "snapshot_id": sid, "expiry_date": expiry, "dte": 7,
        "strike": 6000.0 + i, "right": "C" if i % 2 else "P",
        "bid": 1.0, "ask": 1.2, "mark": 1.1, "last": 1.1, "iv": 0.2,
        "delta": 0.5, "gamma": 0.01, "theta": -0.5, "vega": 1.0,
        "volume": 10, "open_interest": 100,
        "intrinsic_value": 0.0, "time_value": 1.1,
    } for i in range(n)])


def _rows(path: str) -> int:
    with db.get_conn(path) as conn:
        return conn.execute("SELECT COUNT(*) c FROM option_rows").fetchone()["c"]


@pytest.fixture
def prunable_db(temp_db, monkeypatch):
    """A database with 5 prunable rows, wired in as config.DB_PATH."""
    _seed(temp_db)
    monkeypatch.setattr(config, "DB_PATH", temp_db)
    monkeypatch.setattr(prune.config, "DB_PATH", temp_db)
    return temp_db


def _backup(path: str) -> Path:
    """A file the backup guard will accept, newer than the database."""
    src = Path(path)
    dest = src.with_name(src.stem + ".bak")
    dest.write_bytes(src.read_bytes())
    return dest


def test_the_test_database_is_not_the_production_one(prunable_db):
    """If this ever fails, every other test in this file has been deleting
    real data. It is first on purpose.

    Compared against the path captured at import time, before any fixture
    patched it — hardcoding the production path here would go stale silently
    the day the database moves, which is the one day this check must work.
    """
    assert Path(prunable_db).resolve() != Path(REAL_DB_PATH).resolve()
    assert prunable_db == prune.config.DB_PATH


# ─────────────────────────────────────────────────────────────────────────────
# Gate 1 — reporting is the default
# ─────────────────────────────────────────────────────────────────────────────

def test_the_default_run_deletes_nothing(prunable_db, capsys):
    assert prune.main(["--today", TODAY]) == 0
    assert _rows(prunable_db) == 5
    assert "nothing was deleted" in capsys.readouterr().out


def test_the_report_names_the_expiries_and_the_row_count(prunable_db, capsys):
    prune.main(["--today", TODAY])
    out = capsys.readouterr().out
    assert OLD_EXPIRY in out
    assert "5 rows" in out


def test_nothing_to_prune_is_reported_as_normal_not_as_a_failure(temp_db, monkeypatch, capsys):
    """Today's real case. If this exited non-zero or looked like an error, the
    honest answer 'not yet' would read as something needing fixing."""
    _seed(temp_db, expiry="2026-08-01")
    monkeypatch.setattr(prune.config, "DB_PATH", temp_db)
    assert prune.main(["--today", TODAY]) == 0
    assert "NOTHING TO PRUNE" in capsys.readouterr().out


# ─────────────────────────────────────────────────────────────────────────────
# Gate 2 — the backup guard
# ─────────────────────────────────────────────────────────────────────────────

def test_execute_refuses_without_a_backup(prunable_db, capsys):
    assert prune.main(["--today", TODAY, "--execute"]) == 1
    assert _rows(prunable_db) == 5
    assert "REFUSING TO DELETE" in capsys.readouterr().out


def test_a_backup_older_than_the_database_does_not_count(prunable_db, capsys):
    """A stale backup is the dangerous case — it looks like protection and is
    missing exactly the data added since."""
    stale = _backup(prunable_db)
    old = Path(prunable_db).stat().st_mtime - 3600
    os.utime(stale, (old, old))

    assert prune.main(["--today", TODAY, "--execute"]) == 1
    assert _rows(prunable_db) == 5


def test_the_backup_guard_can_be_overridden_explicitly(prunable_db, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda *_: "")   # then cancel at confirm
    prune.main(["--today", TODAY, "--execute", "--no-backup-check"])
    out = capsys.readouterr().out
    assert "REFUSING TO DELETE" not in out
    assert _rows(prunable_db) == 5      # still stopped by the confirmation


# ─────────────────────────────────────────────────────────────────────────────
# Gate 3 — the confirmation
# ─────────────────────────────────────────────────────────────────────────────

def test_a_wrong_answer_cancels(prunable_db, monkeypatch, capsys):
    _backup(prunable_db)
    monkeypatch.setattr("builtins.input", lambda *_: "yes")
    assert prune.main(["--today", TODAY, "--execute"]) == 1
    assert _rows(prunable_db) == 5
    assert "Cancelled" in capsys.readouterr().out


def test_y_does_not_work(prunable_db, monkeypatch):
    """The point of asking for a number. 'y' is what a reflex types."""
    _backup(prunable_db)
    monkeypatch.setattr("builtins.input", lambda *_: "y")
    assert prune.main(["--today", TODAY, "--execute"]) == 1
    assert _rows(prunable_db) == 5


def test_a_closed_stdin_cancels_rather_than_proceeding(prunable_db, monkeypatch):
    """Piped or scheduled, input() raises EOFError. That must mean no, not yes —
    it is the exact circumstance in which nobody is watching."""
    _backup(prunable_db)
    def _eof(*_):
        raise EOFError
    monkeypatch.setattr("builtins.input", _eof)
    assert prune.main(["--today", TODAY, "--execute"]) == 1
    assert _rows(prunable_db) == 5


def test_the_exact_row_count_proceeds(prunable_db, monkeypatch, capsys):
    """The one path that deletes. Without this, every test above is satisfied by
    a script that can never delete anything at all."""
    _backup(prunable_db)
    monkeypatch.setattr("builtins.input", lambda *_: "5")
    assert prune.main(["--today", TODAY, "--execute"]) == 0
    assert _rows(prunable_db) == 0
    assert "Deleted 5 option_rows" in capsys.readouterr().out


def test_a_comma_formatted_count_is_accepted(temp_db, monkeypatch):
    """The report prints 9,901,390. Retyping it WITH the commas is the obvious
    thing to do and must not be read as a wrong answer.

    Needs four figures to have a comma to type at all — with the 5-row fixture
    this test would pass against a script that rejected commas outright.
    """
    _seed(temp_db, n=1500)
    monkeypatch.setattr(prune.config, "DB_PATH", temp_db)
    _backup(temp_db)
    monkeypatch.setattr("builtins.input", lambda *_: "1,500")

    assert prune.main(["--today", TODAY, "--execute"]) == 0
    assert _rows(temp_db) == 0


# ─────────────────────────────────────────────────────────────────────────────
# The trade-protection rule, through the script
# ─────────────────────────────────────────────────────────────────────────────

def test_a_traded_expiry_is_not_deleted_even_at_the_confirmation(prunable_db, monkeypatch, capsys):
    db.init_trades_table(prunable_db)
    db.insert_trade(prunable_db, {
        "trade_id": "T-001", "entry_date": "2026-01-05", "entry_time": "10:00",
        "status": "Open", "contracts": 1, "total_debit": 4.0,
        "initial_legs": json.dumps([{"expiry": OLD_EXPIRY, "type": "Call",
                                     "action": "Sell to Open", "strike": 6000.0,
                                     "fill": 1.0}]),
    })
    _backup(prunable_db)
    monkeypatch.setattr("builtins.input", lambda *_: "5")

    prune.main(["--today", TODAY, "--execute"])

    assert _rows(prunable_db) == 5
    assert "HELD BACK" in capsys.readouterr().out
