"""The opportunity registry — which non-ATM combos crossed the gap threshold, and when.

This is what lets a card say "seen 4x" or show a setup that was live an hour ago
while nobody was watching. Roughly 700 KB and rewritten in full on every new
snapshot, which is why the atomic write in store.py matters more here than
anywhere else.

Honest limitation, unchanged by M2: it only accumulates forward from when the
feature shipped. There is no retroactive backfill, because that would mean
replaying full historical option chains — the expensive thing Mission Control's
caching exists to avoid.

Only load and save live here. Deciding WHAT belongs in the registry (the upsert,
the retention window) is Mission Control's business logic and stays with it.
"""
from __future__ import annotations

from state.store import fingerprint as _fingerprint
from state.store import read_json, write_json

FILENAME = "eligible_history.json"

RETENTION_DAYS = 30


def load(state_dir) -> dict:
    return read_json(state_dir, FILENAME)


def save(state_dir, registry: dict) -> bool:
    return write_json(state_dir, FILENAME, registry)


def fingerprint(state_dir) -> tuple[int, int] | None:
    """A cheap "has this file changed" token — see state/store.fingerprint.

    Here rather than in the caller so the registry's filename stays known to
    exactly one module. A server that built the path itself to stat it would
    be the second place that has to be edited if the file is ever renamed.
    """
    return _fingerprint(state_dir, FILENAME)
