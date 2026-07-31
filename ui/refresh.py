"""Live data refresh — silent, and triggered by change rather than by clock.

WHAT IT REPLACED. `st_autorefresh` forced a FULL-PAGE rerun every
`poll_interval`, which reset charts (losing zoom and pan) and interrupted
analysis even when NO new data had arrived. Instead, a background fragment
polls for a new COMPLETE snapshot on a short timer and reruns the app ONLY
when the snapshot_id actually changes. While the snapshot is unchanged —
mid-analysis, or after hours when the collector is idle — nothing reruns, so
the page stays put.

READ THE COMMENT INSIDE BEFORE CHANGING ANYTHING HERE. This function froze
the entire dashboard at 100% CPU for an unknown number of days (BUG-020), and
the fix is a single line in an order that looks arbitrary and is not.

NO TEST CAN RUN THIS. Streamlit's `AppTest` does not fire fragment timers at
all, which is exactly why the suite never saw BUG-020 and why
`tests/test_layering.py::test_the_refresh_poller_adopts_the_snapshot_before_it_reruns`
asserts on the SOURCE instead. That test was re-pointed here in M2 step 2.5.

Moved out of app.py in that step with ONE line changed: the snapshot lookup
is injected rather than imported, so this module needs neither `db` nor
`config` and the ui/ layer rule holds. Every other line, including the order
that matters, is byte-identical to the version that fixed the bug.
"""
from __future__ import annotations

from collections.abc import Callable

import streamlit as st
from streamlit_autorefresh import st_autorefresh


def install(poll_interval: int, newest_snapshot_id: Callable[[], int | None]) -> None:
    """Start the change-triggered refresh, or fall back to a timed rerun.

    newest_snapshot_id — a zero-argument callable returning the id of the
    newest COMPLETE snapshot, or None. Injected so this module does not need
    to know where the database is (the ui/ rule); app.py binds it to
    `db.get_latest_complete_snapshot(config.DB_PATH)`.
    """
    _LIVE_POLL_SECONDS = max(5, min(int(poll_interval), 20))
    _fragment = getattr(st, "fragment", None) or getattr(st, "experimental_fragment", None)

    if _fragment is not None:
        @_fragment(run_every=_LIVE_POLL_SECONDS)
        def _live_refresh_poller():
            # Cheap: one indexed lookup of the newest COMPLETE snapshot. Triggers a
            # full-app rerun only when new data has landed since the current render.
            try:
                _latest_id = newest_snapshot_id()
            except Exception:
                return
            if "_active_snapshot_id" not in st.session_state:
                # First check of a brand-new session: nothing has rendered yet,
                # so there's nothing to refresh. Adopt the latest snapshot
                # silently. (Rerunning here — before _active_snapshot_id is ever
                # set further down the script — would rerun forever and the
                # page would never render.)
                st.session_state["_active_snapshot_id"] = _latest_id
            elif (_latest_id is not None
                    and _latest_id != st.session_state["_active_snapshot_id"]):
                # ADOPT IT HERE, BEFORE RERUNNING — BUG-020.
                #
                # The comment above describes this exact trap and guards only the
                # first-run case. It applies just as much here, for a reason that
                # is easy to miss: st.rerun() ABORTS the current script run on the
                # spot, and the line that records which snapshot we are showing
                # (`_active_snapshot_id = snapshot_id`) is ~100 lines further down.
                # So it never ran. The next execution called this poller again,
                # found the same stale value, and rerun again — forever, at 100%
                # CPU, never reaching the point where anything is drawn.
                #
                # Recording it here is what makes the rerun happen exactly once
                # per new snapshot. The assignment further down then becomes a
                # harmless restatement of the same value.
                st.session_state["_active_snapshot_id"] = _latest_id
                st.rerun()

        _live_refresh_poller()
    else:
        # Fallback for Streamlit builds without fragment support.
        st_autorefresh(interval=poll_interval * 1000, key="autorefresh")
