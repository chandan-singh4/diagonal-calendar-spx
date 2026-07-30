"""Entry locks — the price a diagonal was actually filled at.

Once a diagonal is filled, "what would a new diagonal cost today" stops being
the useful number; the trader only cares how the FIXED entry compares to the
live Transform Order Mark. Locking the entry mark is what lets Chart 1 switch
from "hypothetical entry" to "position management" for that combo.

Forward-compat note (do not build yet — the Journal is out of scope): each lock
carries a stable `lock_id` and a `journal_trade_id` that stays null under
"Monitor Only". When Journal integration is scoped, a "Monitor + Log Trade" path
can create the Journal row and write its id back here — the lock stays the
single source of truth for entry price and time, rather than the Journal and
this file drifting into two independent copies of the same fact.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from state.store import read_json, write_json

FILENAME = "entry_locks.json"


def key(front_expiry: str, back_expiry: str,
        put_strike: float, call_strike: float) -> str:
    return f"{front_expiry}|{back_expiry}|{put_strike:.0f}|{call_strike:.0f}"


def load(state_dir) -> dict:
    return read_json(state_dir, FILENAME)


def save(state_dir, locks: dict) -> bool:
    return write_json(state_dir, FILENAME, locks)


def get(state_dir, front_expiry: str, back_expiry: str,
        put_strike: float, call_strike: float) -> dict | None:
    return load(state_dir).get(key(front_expiry, back_expiry, put_strike, call_strike))


def create(state_dir, front_expiry: str, back_expiry: str, put_strike: float,
           call_strike: float, *, diagonal_mark: float, mode: str,
           display_tz: str) -> dict:
    """mode is 'monitor_only' or 'monitor_and_log' (the latter is scaffolded
    only — no Journal row is created yet, see the module note above).

    display_tz is passed in rather than read from config so this module has no
    hidden global input — the same rule the database path now follows.
    """
    locks = load(state_dir)
    record = {
        "lock_id": str(uuid.uuid4()),
        "front_expiry": front_expiry,
        "back_expiry": back_expiry,
        "put_strike": put_strike,
        "call_strike": call_strike,
        "entry_diagonal_mark": diagonal_mark,
        "locked_at": datetime.now(ZoneInfo(display_tz)).isoformat(),
        "mode": mode,
        "journal_trade_id": None,
    }
    locks[key(front_expiry, back_expiry, put_strike, call_strike)] = record
    save(state_dir, locks)
    return record


def clear(state_dir, front_expiry: str, back_expiry: str,
          put_strike: float, call_strike: float) -> None:
    locks = load(state_dir)
    k = key(front_expiry, back_expiry, put_strike, call_strike)
    if k in locks:
        del locks[k]
        save(state_dir, locks)


def update_mark(state_dir, front_expiry: str, back_expiry: str, put_strike: float,
                call_strike: float, *, new_mark: float) -> None:
    """Correct a locked entry price without disturbing lock_id/locked_at/mode —
    this is a fix to a fat-fingered fill price, not a new entry."""
    locks = load(state_dir)
    k = key(front_expiry, back_expiry, put_strike, call_strike)
    if k in locks:
        locks[k]["entry_diagonal_mark"] = new_mark
        save(state_dir, locks)
