"""
tutor.py — POST /mission/ask. The chat panel's one endpoint.

WHAT IT SERVES. A question, the conversation so far, and optionally a
snapshot id; back comes an answer written from that snapshot's assembled
ladder and nothing else. The ladder is `core.briefing.assemble` over
`api.computed` — the same five exposures the gamma panels draw and the same
ones the Telegram briefing is built from — so the chat cannot develop a
private opinion about what is on screen.

IT SHARES THE EXPOSURE CACHE WITH THE CHARTS. The five `ctx.cached` keys
below are deliberately the ones `reads.build_computed_router` already uses,
so asking a question about a snapshot the reader is looking at recomputes
nothing. The cache is keyed on the snapshot, so a new one invalidates both.

DEFINED WITH `def`, NOT `async def`, AND THAT IS LOAD-BEARING. `llm.complete`
is synchronous httpx and walks a fallback chain that can take the better part
of a minute on a bad free tier. Declared async it would block the event loop
for that whole time, freezing the snapshot websocket and every other request
in the process. FastAPI runs a plain `def` route in a threadpool, which is
exactly what a slow blocking call wants.

NOTHING HERE IS WRITTEN DOWN. Unlike the scheduled briefing, an answer to a
chat question is not scored and not logged: it is a reading aid, and a
transcript of it on disk would be a second record of the day competing with
`briefing_forecasts.jsonl`. The conversation lives in the browser.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import config
import db
from api import computed
from api.reads import ReadContext
from core import briefing as core_briefing
from core import tutor as core_tutor
from dataaccess import queries
from integrations import llm

# The longest question that will be accepted. Not a safety measure — the
# figures dwarf it either way — but a paste of an entire article is a question
# the tutor cannot answer from a snapshot, and it is kinder to say so at the
# door than to spend a minute of the free tier discovering it.
MAX_QUESTION = 2_000

# How much conversation the client may send back. `core.tutor` trims to the
# last few exchanges regardless; this stops an unbounded body arriving at all.
MAX_HISTORY = 40

# How long a fetched roster is reused. The three rosters are three HTTP calls
# to three providers and they change on the order of days, so fetching them
# for every panel open would add a second of latency to a list that had not
# moved. Short enough that a model appearing on the free tier this morning is
# selectable this afternoon without a restart.
ROSTER_TTL_SECONDS = 900


class Turn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION)
    history: list[Turn] = Field(default_factory=list, max_length=MAX_HISTORY)
    # THE PICK IS A HEAD, NOT A LOCK. It is promoted to the front of the
    # fallback chain rather than replacing it, because every model here is on
    # a free tier and free tiers go busy. A panel that returned "your model is
    # unavailable" instead of an answer from the next rung would be honest and
    # useless; the response says which model actually answered instead.
    model: str | None = None
    effort: str = Field(default=core_tutor.DEFAULT_EFFORT,
                        pattern="^(low|medium|high|max)$")
    # OPTIONAL, AND DEFAULTS TO THE NEWEST. The panel sends the snapshot the
    # reader is actually looking at, so a question asked about a replayed
    # board is answered from that board rather than from this minute's.
    snapshot_id: int | None = None


def _ladder(ctx: ReadContext, target: int, chain: pd.DataFrame,
            spot: float, snapshot_ts: str) -> dict:
    """The five rungs, through the cache the charts already filled."""
    shared = dict(r=config.RISK_FREE_RATE, q=config.DIVIDEND_YIELD,
                  snapshot_ts=snapshot_ts,
                  display_tz=config.DISPLAY_TIMEZONE)
    second = dict(r=config.RISK_FREE_RATE, q=config.DIVIDEND_YIELD,
                  display_tz=config.DISPLAY_TIMEZONE)
    return core_briefing.assemble(
        spot=spot,
        session_time=core_tutor.market_clock(
            snapshot_ts, config.DISPLAY_TIMEZONE),
        gamma=ctx.cached(("gamma", target, None),
                         lambda: computed.gamma_exposure(
                             chain, spot, None, **shared)),
        vgex=ctx.cached(("vgex", target, None),
                        lambda: computed.volume_gamma_exposure(
                            chain, spot, None, **shared)),
        delta=ctx.cached(("delta", target, None),
                         lambda: computed.delta_exposure(chain, spot, None)),
        vanna=ctx.cached(("second-order", target, None, "vanna"),
                         lambda: computed.second_order_exposure(
                             chain, spot, "vanna", snapshot_ts,
                             expiry=None, **second)),
        charm=ctx.cached(("second-order", target, None, "charm"),
                         lambda: computed.second_order_exposure(
                             chain, spot, "charm", snapshot_ts,
                             expiry=None, **second)),
    )


def build_router(ctx: ReadContext) -> APIRouter:
    router = APIRouter(prefix="/mission", tags=["tutor"])

    @router.post("/ask", summary="Ask about the snapshot on screen")
    def ask(body: Question) -> dict[str, Any]:
        target = (body.snapshot_id if body.snapshot_id is not None
                  else ctx.generation())
        if target is None:
            raise HTTPException(
                status_code=503,
                detail="No completed snapshot exists to read yet.")

        chain = ctx.cached(("chain", target),
                           lambda: queries.load_chain_df(ctx.db_path, target))
        if chain.empty:
            raise HTTPException(
                status_code=404,
                detail=f"Snapshot {target} holds no option rows to read.")

        row = db.get_snapshot_by_id(ctx.db_path, target)
        if row is None or row["underlying_price"] is None:
            raise HTTPException(
                status_code=422,
                detail=f"Snapshot {target} records no underlying price, so "
                       f"nothing priced against spot can be read from it.")

        spot = float(row["underlying_price"])
        figures = _ladder(ctx, target, chain, spot, row["snapshot_timestamp"])
        history = [t.model_dump() for t in body.history]

        try:
            answer = llm.complete(
                core_tutor.SYSTEM_PROMPT,
                core_tutor.user_turn(body.question, history, figures,
                                     body.effort),
                preferred=body.model,
                # THE SAME WORD DOES TWO JOBS: it rides to the provider as a
                # reasoning budget and it shapes the written answer through
                # the prompt above. A reader who asks for MAX wants both.
                effort=body.effort)
        except llm.LLMError as exc:
            # 503, NOT 500. Every free tier being busy at once is an ordinary
            # event and the fix is to ask again in a minute — which is what a
            # 503 tells the panel, and what a 500 would not.
            raise HTTPException(
                status_code=503,
                detail=f"No model answered: {exc}") from exc

        return {
            "answer": answer.text,
            "provider": answer.provider,
            "model": answer.model,
            # TRUE WHEN THE CHAIN FELL PAST THE PICK. The panel says so beside
            # the answer: a reader who chose a model and silently got another
            # would draw conclusions about the wrong one.
            "fell_back": bool(body.model and answer.model != body.model),
            "effort": body.effort,
            "snapshot_id": target,
            # ECHOED SO THE PANEL CAN SHOW WHAT WAS READ. An answer with no
            # visible snapshot behind it is one the reader cannot check, and
            # this chat's whole claim is that it is reading their screen.
            "spot": figures["spot"],
            "session_time": figures["as_of"],
            "pin": figures["pin"],
        }

    _roster_cache: dict[str, Any] = {"at": 0.0, "models": None}

    @router.get("/models", summary="The free models available right now")
    def models() -> dict[str, Any]:
        """The live fallback chain, in the order it would be walked.

        BUILT FROM THE PROVIDERS' OWN ROSTERS, never from a list in this file.
        The free tiers churn weekly — a model hardcoded here would be a
        selectable name that 404s a fortnight later, which is the failure
        `integrations/llm.py` was written to avoid in the first place.
        """
        now = time.monotonic()
        if (_roster_cache["models"] is None
                or now - _roster_cache["at"] > ROSTER_TTL_SECONDS):
            _roster_cache["models"] = [
                {"id": m.id, "provider": m.provider,
                 "name": m.name, "context": m.context}
                for m in llm.chain()
            ]
            _roster_cache["at"] = now
        rows = _roster_cache["models"]
        return {
            "models": rows,
            # The head of the chain, which is what an unset picker uses. Named
            # so the panel can show "Auto (gemini-3.8-flash)" rather than a
            # bare "Auto" that tells the reader nothing about what answered.
            "default": rows[0]["id"] if rows else None,
            "efforts": list(core_tutor.EFFORT),
            # A FINGERPRINT OF THE PROMPT THIS PROCESS ACTUALLY LOADED.
            # Cost an evening once (2026-09-09): the panel and a terminal
            # disagreed about the same question, and there was no way from
            # outside to tell a weak model from a server running yesterday's
            # code. Now `curl /mission/models` answers it.
            "prompt_fingerprint": hashlib.sha256(
                core_tutor.SYSTEM_PROMPT.encode()).hexdigest()[:12],
        }

    return router
