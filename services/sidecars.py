"""The JSON sidecar files, bound to their directory.

THREE SMALL FILES that are the dashboard's only exception to being a pure
reader: chart colours, entry locks, and the eligibility registry. All three
are USER PREFERENCES or DERIVED BOOKKEEPING, never market data — which is why
writing them from the dashboard does not violate the collector/dashboard
read-only split.

WHAT EACH WRAPPER DOES, AND ALL IT DOES: supply `config.STATE_DIR`. The
persistence itself is `state/`, which is deliberately told its directory
rather than reading configuration of its own. That is the DEBT-011 fix
(ADR-035): these were once `Path("eligible_history.json")`, relative, so they
resolved against whatever directory the dashboard happened to be launched
from — start it anywhere but the project root and it found no registry,
created an empty one there, and showed a Mission Control panel that had
silently forgotten every past opportunity.

MOVED HERE IN M2 STEP 2.5, unchanged. What did NOT come with them is the
registry's business logic — the upsert and the retention window are Mission
Control's, not persistence's, and live in services/mission_control.py.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import config
from state import chart_colors, eligible_history, entry_locks

# ─── Constants ────────────────────────────────────────────────────────────────
# SPARK_BARS, the session rangebreaks and break_sessions, and the IV-ratio
# bands now live in core/format.py and core/charts.py — imported at the top of
# this file. Only page-bound state stays here.

# ─── Chart Appearance — user-customizable line colors ─────────────────────────
# Persisted to a small JSON file (same pattern as pinned_pairs.json — this is
# a user-preference file, not market data, so writing it from app.py does not
# violate the collector/dashboard read-only split).
#
# To add color customization for a future line series: add one entry to
# DEFAULT_CHART_COLORS with a (label, hex) tuple. The sidebar picker and the
# reset button both iterate this dict automatically — no other code changes
# needed beyond using CHART_COLORS["your_key"] in the relevant trace.
# The colours themselves and their persistence are state/chart_colors.py.
# config.STATE_DIR is absolute, so these no longer depend on where the dashboard
# was launched from (DEBT-011, ADR-035).
DEFAULT_CHART_COLORS = chart_colors.DEFAULT_CHART_COLORS

def _load_chart_colors() -> dict[str, str]:
    return chart_colors.load(config.STATE_DIR)

def _save_chart_colors(colors: dict[str, str]) -> None:
    chart_colors.save(config.STATE_DIR, colors)

# ─── Entry Locks — post-entry position monitoring ─────────────────────────────
# Same exception-to-pure-reader pattern as chart_colors.json / eligible_history.json.
#
# Purpose: once a diagonal is actually filled, the "current diagonal mark" on
# Chart 1 stops being useful — the trader no longer cares where a *new*
# diagonal would price today, only how the *fixed* entry compares to the
# live Transform Order Mark. Locking a diagonal_mark value at entry time lets
# Chart 1 switch from "hypothetical entry" mode to "position management" mode
# for that specific strike/expiry combo.
#
# Forward-compat note (do not build yet — Journal is explicitly out of scope
# for this round of changes): each lock has a stable `lock_id` and a
# `journal_trade_id` field that stays null under "Monitor Only". When Journal
# integration is scoped, a "Monitor + Log Trade" path can create the Journal
# row and write its id back into this same lock record — the lock stays the
# single source of truth for entry price/time either way, rather than the
# Journal and this file drifting into two independent copies of the same fact.
# Persistence and the lock record itself are state/entry_locks.py; these
# wrappers only supply config.STATE_DIR (ADR-035).

def _entry_lock_key(front_expiry: str, back_expiry: str, put_strike: float, call_strike: float) -> str:
    return entry_locks.key(front_expiry, back_expiry, put_strike, call_strike)

def _load_entry_locks() -> dict:
    """Every reader of the locks file comes through here — the popover, the
    current-combo lookup, the chart. The purge sits INSIDE the loader for that
    reason: filtering the popover alone would tidy the visible list while the
    chart and the lookup carried on seeing a lock whose front leg expired
    weeks ago (BUG-021, ADR-039)."""
    entry_locks.purge_expired(
        config.STATE_DIR,
        now=datetime.now(ZoneInfo(config.DISPLAY_TIMEZONE)),
    )
    return entry_locks.load(config.STATE_DIR)

def _create_entry_lock(front_expiry: str, back_expiry: str, put_strike: float,
                        call_strike: float, diagonal_mark: float, mode: str) -> dict:
    return entry_locks.create(
        config.STATE_DIR, front_expiry, back_expiry, put_strike, call_strike,
        diagonal_mark=diagonal_mark, mode=mode,
        display_tz=config.DISPLAY_TIMEZONE,
    )

def _clear_entry_lock(front_expiry: str, back_expiry: str, put_strike: float, call_strike: float) -> None:
    entry_locks.clear(config.STATE_DIR, front_expiry, back_expiry, put_strike, call_strike)

def _update_entry_lock_mark(front_expiry: str, back_expiry: str, put_strike: float,
                             call_strike: float, new_mark: float) -> None:
    entry_locks.update_mark(config.STATE_DIR, front_expiry, back_expiry,
                             put_strike, call_strike, new_mark=new_mark)

def _get_entry_lock(front_expiry: str, back_expiry: str, put_strike: float, call_strike: float) -> dict | None:
    return _load_entry_locks().get(_entry_lock_key(front_expiry, back_expiry, put_strike, call_strike))

# ─── Eligibility registry — persisted log of non-ATM crossing events ──────────
# A small JSON file (same exception-to-pure-reader pattern as chart_colors.json)
# that accumulates which non-ATM combos have crossed Transform Gap >= 5 and
# when, so "recently eligible" opportunities survive even if no one was
# watching the dashboard when they crossed. Honest limitation: this only
# accumulates going forward from when the feature ships — there's no
# retroactive backfill, since that would require replaying full historical
# option chains, which is the expensive thing Mission Control caching was
# built specifically to avoid.
# Persistence is state/eligible_history.py. What BELONGS in the registry — the
# upsert below and the retention window — is Mission Control's business logic
# and stays here (ADR-035).
_ELIGIBLE_HISTORY_RETENTION_DAYS  = eligible_history.RETENTION_DAYS

def _load_eligible_history() -> dict:
    return eligible_history.load(config.STATE_DIR)

def _save_eligible_history(registry: dict) -> None:
    eligible_history.save(config.STATE_DIR, registry)
