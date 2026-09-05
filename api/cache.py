"""The API's memo — keyed on the snapshot, and on nothing else.

WHY NOT COPY WHAT THE PAGE DOES. `services/loaders.py` is thirteen
`@st.cache_data` wrappers keyed on `snapshot_id` but INVALIDATED BY TTL — 55s,
120s, 300s. Those two facts disagree. A request landing after a lapse redoes a
full query and DataFrame rebuild to produce a byte-identical answer, because
the clock moved rather than the data. ENH-011 already records this on the page
side; the point here is that M4 does not inherit it.

The data changes when the collector writes a new snapshot. That is the only
thing that changes it. So the snapshot IS the key, and there is no expiry: an
entry computed under snapshot 6387 stays correct for exactly as long as 6387
is the newest snapshot, which may be one minute on a Tuesday or all weekend.

HOW INVALIDATION WORKS. Not per-entry. When the newest snapshot_id changes,
every entry from the previous one is dropped at once — they are all stale by
the same event, and expiring them individually would mean holding two
generations of answers and hoping callers do not mix them. A caller that reads
the chain and then the strike metrics gets both from the same snapshot or
neither.

WHAT THIS IS NOT. Not an LRU, and not a size-bounded cache in the general
sense: `max_entries` is a backstop against a client sweeping thousands of
distinct strike/expiry combinations within one snapshot window, not a tuning
knob. If it is ever being hit in normal use, the right response is to look at
what the caller is doing, not to raise the number.

THREAD SAFETY IS NOT OPTIONAL HERE. FastAPI runs `def` (non-async) routes in a
worker threadpool, so two requests genuinely execute at once. The lock guards
the dict and the generation counter together — checking the generation and
then reading the dict without one is a race that serves a snapshot 6386 answer
under a snapshot 6387 label, which is the exact class of fault BUG-029's
"churn verdicts compared two different days" already cost a session.
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

# A sweep of the whole strike grid across every expiry within one snapshot is
# a few hundred distinct keys. This sits above that and well below anything
# that would matter for memory, given entries are DataFrames already held.
DEFAULT_MAX_ENTRIES = 512


class SnapshotCache:
    """Remembers answers for as long as the snapshot they were computed from
    is still the current one."""

    def __init__(self, max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
        self._lock = threading.Lock()
        self._entries: dict[tuple[Any, ...], Any] = {}
        self._generation: Any = None
        self._max_entries = max_entries
        # Plain counters, for /health and for answering "is the cache actually
        # working" with a number rather than an opinion.
        self.hits = 0
        self.misses = 0
        self.invalidations = 0

    def get_or_compute(self, generation: Any, key: tuple[Any, ...],
                       compute: Callable[[], Any]) -> Any:
        """Return the cached answer for `key` under `generation`, or compute it.

        `generation` is the current snapshot_id. `compute` is called at most
        once per (generation, key) under normal conditions — it is
        deliberately called OUTSIDE the lock, so a slow query does not block
        every other request. The cost is that two threads racing on the same
        cold key may both compute it; they produce the same answer from the
        same immutable snapshot, so the duplicate work is wasted but never
        wrong. Holding the lock across the query would trade that for a server
        that serialises every read behind the slowest one.
        """
        with self._lock:
            if generation != self._generation:
                if self._entries:
                    self.invalidations += 1
                self._entries.clear()
                self._generation = generation
            if key in self._entries:
                self.hits += 1
                return self._entries[key]
            self.misses += 1

        value = compute()

        with self._lock:
            # Re-check: the snapshot may have advanced while we queried. If it
            # has, this answer belongs to a generation that is already gone and
            # must not be filed under the new one. Returning it is still right
            # — the caller asked under the old generation and gets a consistent
            # answer — but storing it would poison the new one.
            if generation != self._generation:
                return value
            if len(self._entries) >= self._max_entries:
                self._entries.clear()
            self._entries[key] = value
        return value

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "generation": self._generation,
                "entries": len(self._entries),
                "hits": self.hits,
                "misses": self.misses,
                "invalidations": self.invalidations,
            }

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._generation = None
