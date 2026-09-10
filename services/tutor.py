"""
tutor.py — the Streamlit Ask tab's data layer. Thin, and deliberately so.

THIS IS THE SUNSETTING FACE. The rules, the prompt and the assembly of a turn
all live in `core/tutor.py`, and the FastAPI panel calls the same two
functions. What is left here is the one thing core/ may not do: memoise the
ladder against a Streamlit cache, using this project's config. When the
Streamlit app is retired, this file and views/tutor.py go with it and the
tutor itself is untouched.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import config
from api import computed
from core import briefing as core_briefing
from core import tutor as core_tutor
from integrations import llm


@st.cache_data(show_spinner=False)
def figures(chain_df: pd.DataFrame, spot: float, snapshot_ts: str,
            clock: str) -> dict:
    """The ladder for one snapshot, cached on it.

    Cached because building it runs five exposure calculations over the whole
    chain, and Streamlit reruns the script on every keystroke in the chat box.
    """
    shared = dict(r=config.RISK_FREE_RATE, q=config.DIVIDEND_YIELD,
                  snapshot_ts=snapshot_ts, display_tz=config.DISPLAY_TIMEZONE)
    second = dict(r=config.RISK_FREE_RATE, q=config.DIVIDEND_YIELD,
                  display_tz=config.DISPLAY_TIMEZONE)
    return core_briefing.assemble(
        spot=spot,
        session_time=clock,
        gamma=computed.gamma_exposure(chain_df, spot, None, **shared),
        vgex=computed.volume_gamma_exposure(chain_df, spot, None, **shared),
        delta=computed.delta_exposure(chain_df, spot, None),
        vanna=computed.second_order_exposure(
            chain_df, spot, "vanna", snapshot_ts, **second),
        charm=computed.second_order_exposure(
            chain_df, spot, "charm", snapshot_ts, **second),
    )


def ask(question: str, history: list[dict], figs: dict) -> llm.Completion:
    """One answer. Raises llm.LLMError only when every provider declined."""
    return llm.complete(core_tutor.SYSTEM_PROMPT,
                        core_tutor.user_turn(question, history, figs))
