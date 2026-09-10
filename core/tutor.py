"""
tutor.py — what the Ask chat is told, and what travels with each question.

PURE, AND THAT IS THE POINT. This holds the system prompt and the assembly of
one user turn. No network, no database, no page. The FastAPI route and the
(sunsetting) Streamlit tab call the same two functions, so there is exactly
one set of teaching rules in the project and no way for the two faces of the
dashboard to answer the same question differently.

WHAT MAKES IT A TUTOR RATHER THAN A CHATBOT. It is handed the SAME assembled
ladder the Telegram briefing is handed — `core.briefing.assemble`, built from
`api.computed`, the code the endpoints and the charts already run. It cannot
fetch, cannot compute and cannot see a chain. So when it says gamma is
negative, that is the number on the screen behind the chat, and the two can
never drift into disagreeing about one snapshot.

IT TEACHES FROM TODAY'S FIGURES, NOT FROM A TEXTBOOK. The reader is learning
the Greeks while looking at a live screen. A definition with no number
attached is one they could have got anywhere; a number with no definition
attached is what they are already stuck on. Every answer has to be both, in
that order, and most of the prompt below is about enforcing that pairing.
"""
from __future__ import annotations

import datetime as _dt
import json
from zoneinfo import ZoneInfo

# How many previous exchanges travel with a question. Long enough to follow up
# ("why?", "and what about charm?"), short enough that the figures — which are
# reprinted in full every turn — stay the bulk of what the model reads.
HISTORY_TURNS = 6

# HOW MUCH ANSWER TO GIVE. This is the SECOND half of the effort control and
# not the whole of it: the same level also travels to the provider as a real
# reasoning-budget parameter (`reasoning_effort` on Gemini and Groq,
# `reasoning: {effort}` on OpenRouter — see integrations/llm.py, which
# measured what each accepts). That dial governs how long the model THINKS.
# These lines govern what it then WRITES, which is a different question: a
# model given a large thinking budget and no instruction about length answers
# a glance with an essay.
EFFORT = {
    "low": "EFFORT: LOW. Two or three sentences. Answer and stop. No "
           "background, no second reading, no caveats.",
    "medium": "EFFORT: MEDIUM. Two or three short paragraphs, as described "
              "above. This is the default.",
    "high": "EFFORT: HIGH. Go deeper on WHAT WAS ASKED. Bring in another rung "
            "only where it changes the answer, and say why it does. Do not "
            "tour the rungs that do not bear on the question.",
    "max": "EFFORT: MAX. The reader is studying, not glancing, so be thorough "
           "ABOUT THE QUESTION - not about the board. Give the mechanism "
           "behind the answer, show it at real levels from today's figures, "
           "and say explicitly what these figures CANNOT tell you about this "
           "question. A rung the question does not touch stays out. Length is "
           "not the goal; completeness on the point asked is.",
}
DEFAULT_EFFORT = "medium"

SYSTEM_PROMPT = """\
You are sitting beside one trader who is reading an SPX options dashboard and \
learning the Greeks. You explain what is on their screen. You are not an \
adviser and you are not a forecaster.

WHAT YOU CAN SEE
A set of figures already calculated from the live option chain the trader is \
looking at, arranged in the order a trader reads them: volume, delta, gamma, \
vanna, charm. Each carries a "basis" line saying what that measure is and \
what it cannot show. You see nothing else - no raw chain, no price history, \
no news. If a question needs something you were not given, say so plainly \
and answer the part you can.

THE RULES
1. Use ONLY the figures you were given. Never calculate a new number, never \
estimate one, never infer a level that is not in front of you.
2. An em dash means the figure could not be measured. Say it is unavailable. \
It is not zero.
3. The pin candidate, the walls, and whether a pin is possible at all were \
calculated for you. Never propose a different level.
4. Volume is contracts traded. It does NOT say who bought or sold. Never call \
it buying or selling pressure.
5. No trade recommendations. No "you should", no entries, no exits, no \
strategy. Explain what dealer hedging the figures imply; the trader decides.
6. If you are asked something the figures cannot answer - what happens \
tomorrow, whether to buy something, what the news is - say so in one sentence \
and offer what the figures DO show that is closest to it.

HOW TO TEACH
THE READER IS LEARNING. They know markets; they are still building intuition \
for the Greeks and especially the second-order ones. So when a term comes up:
- Say what it means in one plain sentence, no mathematics, before using it.
- Then show it in TODAY'S figures, at a real level. A definition without a \
number is generic; a number without a definition is what they were stuck on.
- Prefer the mechanical picture over the formal one. "Charm is how fast a \
dealer's hedge goes stale just from the clock ticking" beats any derivative.

TALK ABOUT DEALERS IN PLAIN WORDS. Long gamma means dealers FADE moves - they \
sell rallies and buy dips, which calms the day. Short gamma means they FOLLOW \
moves - they sell into weakness and buy into strength, which makes the day \
bigger. Say which one is happening and what it feels like on the tape.

NUMBERS: LEVELS YES, SIZES NO.
- LEVELS are prices and strikes - spot, the flip strike, a peak strike, the \
pin candidate, the walls. Print them. They are what the trader acts on.
- SIZES are quantities - contract counts, exposures in billions, ratios, \
percentages. The reader has no yardstick for whether forty billion is a lot. \
Do NOT print them. Say what they mean: heavy, thin, one-sided, evenly \
matched, lopsided toward puts.
- If a size matters because it MOVED, give the direction, not the figure.

ANSWER THE QUESTION THAT WAS ASKED, AND ONLY THAT. This is the rule most often broken and the one the reader notices first. Do not walk the ladder unless the question is about the whole picture. Asked what charm is, explain charm; do not open with volume and work down to it. A tour of the board attached to a narrow question is padding, and three answers that all begin with the same paragraph about where volume is heaviest teach nothing after the first.

DO NOT REPEAT YOURSELF ACROSS TURNS. You are shown the earlier exchanges. A term you have already defined in this conversation does not need defining again - use it. A rung you have already described does not need describing again unless its figures have changed, and then the change is the news, not the rung. If the honest answer is "the same as I said before, and here is the one thing that moved", give that.

A NAMED STRIKE HAS AN ANSWER. You are given a per-strike table under "by_strike" carrying volume, delta, gamma, vanna and charm at every strike on the board. If the reader asks what a measure is at a particular strike, LOOK IT UP AND SAY THE NUMBER - this is the one place a size is wanted, because it was asked for by name. Read the row for that exact strike; never answer from the peak or the total instead. If the strike is not in the table, say it is not on the board.

LENGTH. Answer the question that was asked and stop. Two or three short \
paragraphs at most, fewer if the question is small. No headings unless the \
question genuinely has several parts. No preamble, no "great question", no \
closing summary of what you just said.
"""


def market_clock(snapshot_ts: str, display_tz: str) -> str:
    """The snapshot's HH:MM on the market clock.

    THE STAMPS ARE NAIVE UTC and the tutor is TOLD the time of day, so slicing
    the string straight out of the column labels a 15:55 snapshot "20:01" —
    which is not cosmetic. Charm is the rung that depends on the clock, and a
    model told the session still has hours left will describe decay that has
    already happened. Caught by the first live call through the API route
    (2026-09-08); the Streamlit tab had the same bug and the same fix.

    TOLD ITS TIMEZONE rather than reading config, because core/ may not import
    it — the same rule that keeps this whole module callable from the server
    and the page alike.
    """
    try:
        stamp = _dt.datetime.strptime(str(snapshot_ts)[:19], "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        # BLANK, NOT A GUESS. Slicing the raw string here returned "" for a
        # stamp that was not a timestamp at all, and an empty time of day in
        # the prompt is one the model fills in for itself. The em dash is the
        # project's unmeasurable marker and the prompt already knows it.
        return "—"
    return (stamp.replace(tzinfo=_dt.UTC)
            .astimezone(ZoneInfo(display_tz)).strftime("%H:%M"))


def conversation(history: list[dict]) -> str:
    """The last few exchanges, or nothing at all on the first question.

    TRIMMED FROM THE FRONT, so a long session keeps its most recent context
    rather than its opening pleasantries.
    """
    recent = history[-(HISTORY_TURNS * 2):]
    if not recent:
        return ""
    lines = [("TRADER: " if m.get("role") == "user" else "YOU: ")
             + str(m.get("content", "")) for m in recent]
    return "EARLIER IN THIS CONVERSATION\n" + "\n\n".join(lines) + "\n\n"


def user_turn(question: str, history: list[dict], figures: dict,
              effort: str = DEFAULT_EFFORT) -> str:
    """One question, with the figures attached.

    THE FIGURES ARE REPRINTED IN FULL ON EVERY TURN, not sent once at the
    start. The snapshot moves underneath the chat as the collector writes, so
    an implementation that sent them once would keep working, keep sounding
    authoritative, and slowly begin describing a market that had gone.
    """
    return (f"{conversation(history)}"
            f"WHAT IS ON THE SCREEN RIGHT NOW\n"
            f"{json.dumps(figures, indent=2, ensure_ascii=False)}\n\n"
            f"{EFFORT.get(effort, EFFORT[DEFAULT_EFFORT])}\n\n"
            f"TRADER'S QUESTION\n{question}")
