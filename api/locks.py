"""Entry locks over HTTP — the one write ADR-054 allows this package.

WHY THIS IS A SEPARATE MODULE. `api/reads.py` is named for what it promises,
and burying a POST that changes a file on disk among four hundred lines of
queries would make that promise quietly false. The write lives where its name
says so, and `create_app` includes it deliberately.

WHAT IT MAY TOUCH, AND WHAT IT MAY NOT. `entry_locks.json`, a ~1 KB sidecar in
STATE_DIR, and nothing else. Not `data/dashboard.db` — the database stays
read-only to this package, opened with PRAGMA query_only=ON — and not the
Journal: "Monitor + Log Trade" is recorded as an intention on the lock record
and creates no trade row (Chandan's scoping, 2026-09-07).

WRITING A LOCK IS NOT INERT. `collector.py:649` reads this same file through
`core.pins.from_locks` to decide which strikes it fetches next cycle. A lock
created here changes what the record contains from the next cycle onwards,
which is the point — but it means an accidental write is not merely untidy.

THE LOST UPDATE IS REAL AND ACCEPTED. `state.entry_locks.create` is
load-modify-save over the whole dict, and the old Streamlit screen writes the
same file. `state.store.write_json` is atomic, so no reader ever sees half a
file; but if both screens save in the same instant, one lock is silently
dropped. Two screens, one user, one pair of hands — a file lock would be more
machinery than the failure deserves (ADR-054).

WHY `state.` AND NOT `services.sidecars`. `api/` must not import `services/`
(tests/test_layering.py). `services.sidecars` also binds `config.STATE_DIR`
itself, which is exactly the global input `create_app` exists to remove — a
test pointing the server at a temporary directory would have written to the
real file anyway.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

import config
from state import entry_locks

# The two behaviours a lock can be created with. Written here as the API's
# accepted set rather than validated with a free string, so a client that
# invents a third mode is told 422 at the door instead of persisting a value
# nothing downstream knows how to read.
MODES = ("monitor_only", "monitor_and_log")


class LockRequest(BaseModel):
    """The combo, the price, and how the lock should behave.

    The four identifying fields arrive in the body rather than the path
    because they identify the lock jointly — a key built from a subset would
    be a second definition of `entry_locks.key`, which is the copy that
    eventually disagrees.
    """

    front_expiry: str
    back_expiry: str
    put_strike: float
    call_strike: float
    diagonal_mark: float = Field(..., description=
        "The Diagonal Mark at the instant of the click — the fill price this "
        "lock freezes. Sent by the caller rather than recomputed here: the "
        "trader is asserting what they filled at, and the newest snapshot at "
        "the moment the request lands may already be a different one.")
    mode: str = Field("monitor_only", description=
        "monitor_only or monitor_and_log. The second creates no Journal row "
        "today; it records the intention on the lock so the eventual Journal "
        "entry can be built from this record instead of a second one.")


def _record_with_key(k: str, record: dict) -> dict[str, Any]:
    """The stored record plus the key it is filed under.

    Served rather than left to the client to rebuild, for the same reason the
    marks are: `entry_locks.key` has a format (`:.0f` on the strikes) and a
    client re-deriving it would eventually address a lock that exists under a
    name it cannot spell.
    """
    return {"key": k, **record}


def build_router(ctx) -> APIRouter:
    """`ctx` is the ReadContext — used only for its `state_dir`.

    Taking the whole context rather than a bare path keeps this router's
    construction identical to the others in `create_app`, and means it picks
    up the temporary directory a test points the server at without a second
    wiring decision.
    """
    router = APIRouter(prefix="/locks", tags=["locks"])

    def _purged() -> dict:
        """Every live lock, with expired front legs dropped first.

        THE PURGE IS HERE AND NOT IN THE LIST ROUTE ALONE (BUG-021, ADR-039).
        Filtering only what is displayed would tidy the list while a create
        or a delete carried on seeing a lock whose front leg expired weeks
        ago. `purge_expired` does not rewrite the file when nothing expired,
        so this is a read on all but one call a day.
        """
        entry_locks.purge_expired(
            ctx.state_dir,
            now=datetime.now(ZoneInfo(config.DISPLAY_TIMEZONE)))
        return entry_locks.load(ctx.state_dir)

    @router.get("", summary="Every live entry lock")
    def list_locks() -> dict[str, Any]:
        locks = _purged()
        # Newest first: the popover this feeds is a position list, and the
        # position taken most recently is the one being watched.
        ordered = sorted(locks.items(),
                         key=lambda kv: kv[1].get("locked_at", ""), reverse=True)
        return {"locks": [_record_with_key(k, r) for k, r in ordered],
                "count": len(ordered)}

    @router.post("", summary="Lock an entry price for one combo", status_code=201)
    def create_lock(body: LockRequest) -> dict[str, Any]:
        """Freeze the diagonal mark as this combo's entry price.

        REFUSES TO OVERWRITE (409). A second POST for a combo already locked
        would mint a new `lock_id` and a new `locked_at`, quietly destroying
        the time the position was actually taken — and a double-tapped button
        on a phone is the likeliest way to send one. Correcting a
        fat-fingered fill price is a different act with a different verb; it
        is not exposed here yet.
        """
        if body.mode not in MODES:
            raise HTTPException(
                status_code=422,
                detail=f"mode must be one of {', '.join(MODES)}; got {body.mode!r}")

        k = entry_locks.key(body.front_expiry, body.back_expiry,
                            body.put_strike, body.call_strike)
        existing = _purged().get(k)
        if existing is not None:
            raise HTTPException(
                status_code=409,
                detail=f"That combo is already locked at "
                       f"${existing['entry_diagonal_mark']:.2f} "
                       f"(locked {existing['locked_at']}). Clear it first.")

        record = entry_locks.create(
            ctx.state_dir, body.front_expiry, body.back_expiry,
            body.put_strike, body.call_strike,
            diagonal_mark=body.diagonal_mark, mode=body.mode,
            display_tz=config.DISPLAY_TIMEZONE)
        return _record_with_key(k, record)

    @router.delete("", summary="Remove an entry lock")
    def clear_lock(
        front_expiry: str = Query(...),
        back_expiry: str = Query(...),
        put_strike: float = Query(...),
        call_strike: float = Query(...),
    ) -> dict[str, Any]:
        """Back to discovery mode for this combo.

        NOT A 404 WHEN THERE IS NOTHING TO REMOVE. The new screen retries a
        failed request, and a retry that lands after the first attempt
        succeeded would otherwise report an error for a delete that did
        exactly what was asked. `cleared` says which of the two happened.
        """
        k = entry_locks.key(front_expiry, back_expiry, put_strike, call_strike)
        existed = k in _purged()
        entry_locks.clear(ctx.state_dir, front_expiry, back_expiry,
                          put_strike, call_strike)
        return {"key": k, "cleared": existed}

    return router
