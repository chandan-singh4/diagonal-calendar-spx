"""What the collector must keep recording because a lock depends on it.

WHY THIS EXISTS (BUG-022). The collector narrows every snapshot twice: it keeps
only the nearest `MAX_EXPIRY_COUNT` expiries, and only strikes within
`STRIKE_FETCH_WIDTH_POINTS` of spot. Both narrowings are centred on TODAY. A
lock is not — it is fixed at the moment the diagonal was filled. As SPX moves,
a locked strike drifts toward the edge of the window and eventually out of it,
and from then on the lock's legs stop being recorded.

The visible symptom was worse than a gap. The dashboard's "View Chart" button
stages a lock's four values, a guard drops any that are absent from today's
chain (they would otherwise crash the page), and each dropdown silently falls
back to its default. The trader clicked a position they were holding and was
shown a different diagonal, with nothing saying so.

This module answers only "which expiries and strikes must survive the
narrowing", from a locks dict alone. It is pure and has no filesystem, no
config and no clock, so the rule can be tested without a collector, a database
or a broker — the same shape as core/expiry.py.

WHAT IT DELIBERATELY DOES NOT DO. It does not judge whether a lock is expired;
`state.entry_locks.purge_expired` owns that and runs against the same file. A
lock that should have been purged and was not will pin a few extra strikes,
which costs a handful of rows — the safe direction. Pinning is also forward
only: it protects a lock from the next snapshot onward and cannot fill in
history recorded before the lock existed.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core import contract


@dataclass(frozen=True)
class Pins:
    """Values that must not be narrowed away. Empty when nothing is locked."""
    expiries: frozenset[str] = field(default_factory=frozenset)
    strikes:  frozenset[float] = field(default_factory=frozenset)

    def __bool__(self) -> bool:
        return bool(self.expiries or self.strikes)

    @property
    def expiry_dates(self) -> frozenset[str]:
        """The pinned expiries as plain DATES.

        `expiries` holds display keys, so a lock on the third Friday's a.m.
        contract is stored as "2026-08-21 (AM)". The collector narrows a chain
        keyed on the date alone, and matching a key against it would find
        nothing — the pin would silently stop protecting the lock, which is
        the exact failure BUG-022 exists to prevent. Both contracts share a
        date, and fetching the date fetches both, so the date is the right
        unit here.
        """
        return frozenset(contract.date_of(e) for e in self.expiries)


def from_locks(locks: dict) -> Pins:
    """Collect every expiry and strike any lock depends on.

    A MALFORMED RECORD IS SKIPPED, NOT RAISED. This runs inside the collector's
    snapshot cycle, and the cycle is the thing that must not stop: a lock file
    hand-edited into nonsense should cost that one lock its protection, never
    the whole snapshot. The same refusal as purge_expired's unparseable
    front_expiry — degrade narrowly, stay noisy elsewhere.

    Strikes are floats to match the chain DataFrame's dtype; a strike stored as
    the string "7200" is accepted and converted, because the sidecar file is
    JSON and has been hand-edited before.
    """
    expiries: set[str] = set()
    strikes:  set[float] = set()

    if not isinstance(locks, dict):
        return Pins()

    for record in locks.values():
        if not isinstance(record, dict):
            continue
        for key in ("front_expiry", "back_expiry"):
            value = record.get(key)
            if isinstance(value, str) and value:
                expiries.add(value)
        for key in ("put_strike", "call_strike"):
            value = record.get(key)
            if value is None or isinstance(value, bool):
                continue
            try:
                strikes.add(float(value))
            except (TypeError, ValueError):
                continue

    return Pins(expiries=frozenset(expiries), strikes=frozenset(strikes))
