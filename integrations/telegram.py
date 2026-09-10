"""
telegram.py — posting the briefings to a phone.

WHY TELEGRAM AND NOT DISCORD, recorded because the choice looks arbitrary and
is not. Sending is equally easy on both. RECEIVING is not: a Discord bot needs
either a permanently open gateway websocket or a public HTTPS endpoint for
interactions, and this runs on a home Windows machine behind a router.
Telegram's `getUpdates` is OUTBOUND POLLING — the bot asks Telegram whether
anything has arrived — so it needs no port forwarding, no tunnel and no
inbound firewall rule, which matches how the collector already runs here.

PLAIN TEXT, NO MARKDOWN. Telegram's MarkdownV2 requires escaping sixteen
characters, and an unescaped one does not degrade — the whole message is
REJECTED with a 400. A briefing is full of them: `7,700 (Put)`, `-2.3x`,
`-34.0B`, an em dash on every unmeasurable figure. The choice is between
formatting that works most days and text that works every day, and a briefing
that silently fails to arrive is worse than one without bold headings.

**IT NEVER RAISES INTO THE DAEMON.** Every function here reports failure by
returning False. A phone notification is the least important thing this
project does, and a Telegram outage must not be able to stop a briefing being
computed, logged and scored — the log is the record, the message is a
convenience.

THE TOKEN IS A PASSWORD. It goes in `.env` beside the LLM keys and is never
logged: anyone holding it can post to your channels as you, and read
everything the bot can see.
"""
from __future__ import annotations

import os

import httpx

_API = "https://api.telegram.org/bot{token}/{method}"

# Telegram's hard limit on one message. Not a guideline — a longer message is
# rejected outright, and briefings routinely run past it once the ladder has
# six rungs to describe.
MAX_MESSAGE = 4096

# Left under the limit on purpose. The split points are paragraph boundaries,
# so a chunk lands wherever the prose allows rather than exactly on the
# ceiling; the margin is what stops a rounding error at the boundary rejecting
# a whole briefing for the sake of two characters.
CHUNK = 3800

TIMEOUT_SECONDS = 20.0


def token() -> str | None:
    return (os.environ.get("TELEGRAM_BOT_TOKEN", "") or "").strip() or None


def report_chat() -> str | None:
    return (os.environ.get("TELEGRAM_REPORT_CHAT_ID", "") or "").strip() or None


def configured() -> bool:
    """Whether posting is possible at all.

    Checked by the caller BEFORE a briefing runs so that "Telegram is not set
    up" is said once at start-up rather than fifteen times a day, and so that
    an unconfigured install is silent rather than noisy — the daemon is
    perfectly useful with the log alone.
    """
    return token() is not None and report_chat() is not None


def split(text: str, limit: int = CHUNK) -> list[str]:
    """One long briefing as several sendable messages.

    SPLIT ON MEANING WHERE POSSIBLE, and the order of preference is the point:
    a blank line first, then a single newline, then — only if one paragraph is
    itself too long — mid-text. A briefing cut mid-sentence between two phone
    notifications is much harder to read than one cut between sections, and
    the sections are exactly what the prompt asks the model to write.

    Returns a single-item list for ordinary text, so callers need no branch.
    """
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    rest = text
    while len(rest) > limit:
        window = rest[:limit]
        # rfind returns -1 when absent, which is why each is tested rather
        # than chained with `or` — a -1 would slice from the end.
        cut = window.rfind("\n\n")
        if cut <= 0:
            cut = window.rfind("\n")
        if cut <= 0:
            cut = limit
        chunks.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        chunks.append(rest)
    return chunks


def send(text: str, *, chat_id: str | None = None) -> bool:
    """Post one message, splitting it if it is long. False if anything failed.

    PARTIAL DELIVERY COUNTS AS FAILURE. If the third chunk of four is
    rejected, this returns False even though three arrived — a briefing
    missing its conclusion has lost the part the reader wanted, and reporting
    success would hide that.
    """
    bot = token()
    chat = chat_id or report_chat()
    if bot is None or chat is None:
        return False

    ok = True
    for chunk in split(text):
        try:
            reply = httpx.post(
                _API.format(token=bot, method="sendMessage"),
                json={"chat_id": chat, "text": chunk,
                      # The link preview would attach a card to any URL the
                      # model happens to mention, pushing the briefing itself
                      # off the screen.
                      "disable_web_page_preview": True},
                timeout=TIMEOUT_SECONDS,
            )
        except httpx.HTTPError:
            return False
        if reply.status_code != 200:
            ok = False
            # Kept going rather than returned: the remaining chunks may well
            # succeed, and a briefing missing its middle is more use than one
            # missing everything after the first failure.
            continue
    return ok


def whoami() -> dict | None:
    """The bot's own identity, or None. Used by the setup script to prove the
    token works before anything else is diagnosed."""
    bot = token()
    if bot is None:
        return None
    try:
        reply = httpx.get(_API.format(token=bot, method="getMe"),
                          timeout=TIMEOUT_SECONDS)
    except httpx.HTTPError:
        return None
    if reply.status_code != 200:
        return None
    return reply.json().get("result")


def recent_chats() -> list[dict]:
    """Every chat that has messaged the bot lately, for the setup script.

    THE ONLY WAY TO LEARN A CHAT ID. Telegram does not publish them; a chat
    reveals its id when something is sent from it. That is why setup means
    "message your bot, then run this" and cannot mean "paste the channel
    name".

    Empty is the normal state of a bot nobody has written to, and also what a
    bot returns once its updates have been consumed — `getUpdates` is a queue,
    and reading it twice gives the second reader nothing.
    """
    bot = token()
    if bot is None:
        return []
    try:
        reply = httpx.get(_API.format(token=bot, method="getUpdates"),
                          timeout=TIMEOUT_SECONDS)
    except httpx.HTTPError:
        return []
    if reply.status_code != 200:
        return []

    seen: dict[str, dict] = {}
    for update in reply.json().get("result", []):
        # A channel post and an ordinary message carry the chat in different
        # keys, and a channel is the more likely destination here.
        for key in ("message", "channel_post", "edited_message"):
            chat = (update.get(key) or {}).get("chat")
            if chat and "id" in chat:
                seen[str(chat["id"])] = chat
    return list(seen.values())
