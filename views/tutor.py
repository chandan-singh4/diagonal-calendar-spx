"""
tutor.py — Ask tab. A chat that can only talk about what is on the screen.

WHY IT IS A TAB AND NOT A SIDEBAR PANEL. The answer depends entirely on which
snapshot is loaded, and a panel that floats over every tab invites the
question "explain this chart" while pointing at a chart the model cannot see.
A tab is honest about its scope: it reads the same assembled ladder the Gamma
Exposure tab draws and the Telegram briefing sends, and nothing else.

THE HISTORY LIVES IN SESSION STATE, KEYED BY SNAPSHOT. When the collector
writes a new snapshot the conversation is kept but the figures underneath it
change, so every answer is built from the CURRENT numbers with the previous
exchanges as context — never from the numbers that were on screen when the
question before it was asked.

THIS FILE DRAWS AND NOTHING ELSE. The figures come from services.tutor, the
model call comes from services.llm, and both are reachable from a terminal
without Streamlit running — which is how they are tested.
"""
from __future__ import annotations

import streamlit as st

import config
from core import tutor as core_tutor
from integrations import llm
from services import tutor
from views.context import ViewContext

_HISTORY_KEY = "tutor_history"

# Offered as buttons because the hardest part of learning this tab is not
# understanding the answer, it is knowing what to ask. Each one is a question
# the figures can genuinely answer.
STARTERS = (
    "What is the picture right now, in plain English?",
    "Are dealers long or short gamma, and what does that mean for me?",
    "What is charm, and what is it doing today?",
    "What is vanna, and what is it telling me today?",
    "Which two readings disagree with each other right now?",
)


def render(ctx: ViewContext) -> None:
    st.subheader("🎓  Ask")
    st.caption(
        "A tutor that can see this snapshot's figures and nothing else — no "
        "news, no price history, no chain. It will not recommend trades."
    )

    if ctx.chain_df is None or ctx.chain_df.empty:
        st.info("This snapshot holds no option rows, so there is nothing to "
                "read yet.")
        return

    clock = core_tutor.market_clock(ctx.snapshot_ts, config.DISPLAY_TIMEZONE)
    try:
        figs = tutor.figures(ctx.chain_df, float(ctx.spx_price),
                             ctx.snapshot_ts, clock)
    except Exception as exc:
        # SHOWN, NOT SWALLOWED. A failure here means the ladder could not be
        # assembled, which is the same failure the Gamma Exposure tab would
        # hit — worth seeing rather than hiding behind an empty chat box.
        st.error(f"The figures for this snapshot could not be assembled: {exc}")
        return

    history: list[dict] = st.session_state.setdefault(_HISTORY_KEY, [])

    # ── What it can see, stated up front ─────────────────────────────────
    pin = figs["pin"]
    verdict = ("no pin — moves extend" if not pin["pin_possible"]
               else f"pin candidate {pin['candidate']}, "
                    f"{pin['support']}–{pin['resistance']}")
    left, right = st.columns([3, 1])
    left.markdown(f"**Spot {figs['spot']}**  ·  {clock}  ·  {verdict}")
    if right.button("Clear chat", use_container_width=True,
                    disabled=not history):
        st.session_state[_HISTORY_KEY] = []
        st.rerun()

    for message in history:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    # STARTERS ONLY WHILE THE CHAT IS EMPTY. Once there is a conversation the
    # follow-up is obvious and the buttons are just clutter above it.
    asked = None
    if not history:
        st.markdown("**Not sure what to ask?**")
        for i, starter in enumerate(STARTERS):
            if st.button(starter, key=f"tutor_starter_{i}",
                         use_container_width=True):
                asked = starter

    asked = st.chat_input("Ask about what is on the screen…") or asked
    if not asked:
        return

    with st.chat_message("user"):
        st.markdown(asked)

    with st.chat_message("assistant"):
        with st.spinner("Reading the figures…"):
            try:
                answer = tutor.ask(asked, history, figs)
            except llm.LLMError as exc:
                # ASKED AGAIN IS THE FIX, so the question is not appended to
                # the history — a failed turn must not sit in the transcript
                # as though it had been answered.
                st.warning(f"No model answered just now: {exc}")
                return
        st.markdown(answer.text)
        st.caption(f"{answer.provider} / {answer.model}")

    history.append({"role": "user", "content": asked})
    history.append({"role": "assistant", "content": answer.text})
    st.session_state[_HISTORY_KEY] = history
