"""
llm.py — the one place this project talks to a language model.

THE PATTERN IS READING BUDDY'S, ported rather than copied (`reading-buddy/
api/models.ts`, which carries the long version of every note below). That
project learned these lessons against the live APIs over weeks, and there is
no reason for this one to learn them again.

## Nothing is hardcoded, because the free roster churns weekly

Models are delisted without warning. A list of model ids baked into this file
would slowly become a menu of things that no longer answer, and it would fail
as a 404 in the middle of a session rather than at the moment it went stale.
So the roster is READ LIVE from every provider we hold a key for.

## Three providers, one request shape

OpenRouter, Groq and Gemini all speak the OpenAI chat-completions shape —
Gemini through its compatibility layer at `/v1beta/openai`. That is what makes
three providers affordable: only the base URL and the key change. It is also
why the bespoke `generateContent` call this file used first was deleted; two
request shapes for one job is two places to fix a bug.

## FREE MEANS SOMETHING DIFFERENT ON EACH PROVIDER

Worth stating plainly, because it is the one thing that cannot be read off a
single field. On OpenRouter free is a price of zero and is in the roster. On
Groq and Gemini there is no per-model price at all — the free tier is a
property of the ACCOUNT, and every model is billed at its listed rate the
moment a card is added. So the roster cannot tell us what is free there, and
nothing here pretends it can. What it can do is refuse to spend: only models
the roster marks free on OpenRouter are offered, and on the other two the
account is the control.

## Dead against busy, and why one refusal never deletes a model

    400, 403, 404          gone for good — retired, gated, or wrong-shaped
    429 naming `limit: 0`  gone for good — the free tier grants no quota
    anything else          BUSY, not dead. Step past it and try the next.

Reading Buddy measured this rather than assuming it: probing the same roster
twice minutes apart disagreed on three models. Deleting on one failure throws
away good models for being busy in the second we happened to ask.

## Keys never leave the server

Read from the environment here, never returned, never logged, never placed in
a payload this project serves. If a briefing is ever shown in the browser it
arrives as generated TEXT from our own API — anything in the bundle is in
every screenshot of it.

## What comes back is not a measurement

Prose, and the name of the model that wrote it. Nothing here touches the
database. Generated text must not be stored beside collected data.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

# Where each provider lists its models, and where it answers questions. The
# chat URL is the OpenAI-compatible one on all three — see the header.
ENDPOINTS: dict[str, dict[str, str]] = {
    "gemini": {
        "roster": "https://generativelanguage.googleapis.com/v1beta/models"
                  "?pageSize=200",
        "chat": "https://generativelanguage.googleapis.com/v1beta/openai/"
                "chat/completions",
    },
    "openrouter": {
        "roster": "https://openrouter.ai/api/v1/models",
        "chat": "https://openrouter.ai/api/v1/chat/completions",
    },
    "groq": {
        "roster": "https://api.groq.com/openai/v1/models",
        "chat": "https://api.groq.com/openai/v1/chat/completions",
    },
}

# The environment variable holding each provider's key. A provider we hold no
# key for contributes nothing and costs nothing — it is simply absent from the
# chain, with no error and no empty column.
KEY_NAMES = {
    "gemini": "GEMINI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "groq": "GROQ_API_KEY",
}

# The column order the chain rotates through. Gemini first because Chandan's
# preference is a Gemini Flash and the chain starts at the pick's own column.
PROVIDERS = ("gemini", "openrouter", "groq")

# Models that are not going to read an options dashboard, whatever else they
# are good at. Reading Buddy's list, minus the tutor-specific entries: these
# announce themselves as single-purpose in their own id, and a narrow model
# does not FAIL at the wrong job — it answers confidently in the wrong genre,
# which is far worse here than an error would be.
NOT_AN_ANALYST = re.compile(
    r"image|tts|audio|speech|whisper|orpheus|lyria|robotics|computer-use|"
    r"deep-research|nano-banana|omni|guard|embedding|rerank|moderation|"
    r"content-safety|-code|coder",
    re.IGNORECASE,
)

# The smallest context worth offering. The ladder plus the previous briefing's
# figures runs a few thousand tokens; anything under this is a model that will
# start dropping the top of the prompt, which here means dropping the volume
# rung and answering from gamma alone without saying so.
MIN_CONTEXT = 16_000

# What Chandan asked for first: Gemini's current Flash. A PREFERENCE MATCHED
# AGAINST THE LIVE ROSTER, not an id — written as a pattern precisely because
# the version number moves and a pinned "gemini-3.8-flash" would 404 the week
# Google renames it. The newest matching id wins; if none matches, the chain
# simply starts at the top of the Gemini column instead.
PREFERRED = re.compile(r"gemini-[\d.]+-flash$", re.IGNORECASE)

# Long enough for a slow free-tier queue, short enough that one hung provider
# does not eat the whole chain's budget.
TIMEOUT_SECONDS = 60.0

# ── reasoning effort ─────────────────────────────────────────────────────────
# ALL THREE PROVIDERS TAKE ONE, and they take it under two different names:
# OpenRouter wants an object, Groq and Gemini want the bare word. Both are sent
# to every model rather than guessed at from the slug, because no roster says
# which models think out loud — one that does not simply ignores the field.
#
# THE ACCEPTED VALUES ARE PER MODEL, NOT PER PROVIDER, and that is the thing
# worth knowing here. Measured against the live APIs on 2026-09-08:
#
#   gemini-flash-lite-latest   minimal, low, medium, high, none
#   groq openai/gpt-oss-20b    low, medium, high
#   groq qwen/qwen3.6-27b      none, default          <- a different ladder
#   openrouter nemotron-3.5    the whole ladder, max included
#
# So there is NO TABLE HERE. A hardcoded map of model to accepted values would
# be wrong within a fortnight on rosters that churn weekly, which is the exact
# failure this module was written to avoid. Instead the cap below is a first
# guess and `_ask` retries once without the field when a model rejects it —
# self-correcting, and it costs one extra round trip on the models that need it.
EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")

# The ceiling that most models turned out to share. `max` and `xhigh` collapse
# onto it for the two providers measured to reject them; OpenRouter took the
# whole ladder and keeps it.
CEILING = "high"

# Said in a refusal when the effort itself was the problem, rather than the key,
# the model or the rate limit. All three providers name the field in the
# message, which is what makes the retry below possible without a table.
EFFORT_REJECTED = re.compile(r"reasoning[_ ]effort", re.I)

# The most a single answer may run to, and it is a guard rather than a budget.
# A reasoning model can spend its ENTIRE allowance thinking and then return
# finish_reason "length" with an empty string — an empty answer, not an error,
# which the chain would read as a refusal and charge to the next provider.
# Generous enough that the thinking and the answer both fit.
MAX_TOKENS = 8_192


def _effort_body(provider: str, effort: str | None) -> dict:
    """The reasoning-effort field, in the spelling this provider accepts."""
    if not effort:
        return {}
    if provider == "openrouter":
        # `exclude: false` asks for the working-out to be returned. We drop it
        # in `_strip_reasoning`, but a provider told to hide it sometimes hides
        # the answer with it.
        return {"reasoning": {"effort": effort, "exclude": False}}
    return {"reasoning_effort":
            CEILING if effort in ("xhigh", "max") else effort}

# How long to wait for a roster. Much shorter than a completion: a provider
# that cannot list its models in this long is not going to answer one either,
# and three rosters are fetched before anything useful happens.
ROSTER_TIMEOUT_SECONDS = 15.0


@dataclass(frozen=True)
class Model:
    """One rung of the chain."""
    id: str
    provider: str
    context: int
    name: str = ""


@dataclass(frozen=True)
class Completion:
    """What came back, and who gave it.

    `attempts` carries the rungs that declined on the way, with the reason
    each gave. It is printed with every briefing on purpose: an answer from
    the fourth model in the chain is a different thing from an answer from the
    first, and a reader comparing two briefings needs to know which they hold.
    """
    text: str
    provider: str
    model: str
    attempts: list[str] = field(default_factory=list)


class LLMError(RuntimeError):
    """Every model in the chain declined, or there was no chain to try."""


def _key(provider: str) -> str | None:
    return (os.environ.get(KEY_NAMES[provider], "") or "").strip() or None


def _envelope(payload: Any) -> dict[str, Any] | None:
    """The error object, whatever shape the provider wrapped it in.

    OpenRouter and Groq return `{"error": {...}}`. Gemini's compatibility
    layer returns `[{"error": {...}}]` — a JSON ARRAY holding one of them.
    Reading only the object shape silently loses Gemini's message, and
    `limit: 0` lives in that message: it is the one signal separating "this
    model has no free quota, ever" from "this model is busy this second".
    """
    said = payload[0] if isinstance(payload, list) and payload else payload
    if isinstance(said, dict):
        error = said.get("error")
        if isinstance(error, dict):
            return error
    return None


def _permanent(status: int, message: str) -> bool:
    """Whether a refusal is worth never retrying. See the header's table."""
    if status in (400, 403, 404):
        return True
    return status == 429 and re.search(r"limit:\s*0\b", message) is not None


# ── the rosters ──────────────────────────────────────────────────────────────

def _gemini_roster(key: str) -> list[Model]:
    """The Gemini models that can hold a conversation.

    `generateContent` is the method a chat model supports; the image, speech
    and embedding models list something else. The `models/` prefix is stripped
    because the OpenAI-compatible endpoint does not take it.
    """
    reply = httpx.get(ENDPOINTS["gemini"]["roster"], params={"key": key},
                      timeout=ROSTER_TIMEOUT_SECONDS)
    reply.raise_for_status()
    out = []
    for row in reply.json().get("models", []):
        name = row.get("name")
        methods = row.get("supportedGenerationMethods") or []
        if not isinstance(name, str) or "generateContent" not in methods:
            continue
        out.append(Model(id=name.removeprefix("models/"), provider="gemini",
                         context=int(row.get("inputTokenLimit") or 0),
                         name=str(row.get("displayName") or name)))
    return out


def _openrouter_roster(key: str) -> list[Model]:
    """OpenRouter's FREE models only — zero prompt price and zero completion.

    This is the one provider where the roster can answer the free question,
    and it is asked strictly. A model priced at anything is not offered, so
    the account cannot be spent from here by accident.
    """
    reply = httpx.get(ENDPOINTS["openrouter"]["roster"],
                      headers={"Authorization": f"Bearer {key}"},
                      timeout=ROSTER_TIMEOUT_SECONDS)
    reply.raise_for_status()
    out = []
    for row in reply.json().get("data", []):
        price = row.get("pricing") or {}
        try:
            free = (float(price.get("prompt", 1)) == 0
                    and float(price.get("completion", 1)) == 0)
        except (TypeError, ValueError):
            free = False
        if not free or not isinstance(row.get("id"), str):
            continue
        out.append(Model(id=row["id"], provider="openrouter",
                         context=int(row.get("context_length") or 0),
                         name=str(row.get("name") or row["id"])))
    return out


def _groq_roster(key: str) -> list[Model]:
    """Groq's active models. Price is not consulted — see the header."""
    reply = httpx.get(ENDPOINTS["groq"]["roster"],
                      headers={"Authorization": f"Bearer {key}"},
                      timeout=ROSTER_TIMEOUT_SECONDS)
    reply.raise_for_status()
    out = []
    for row in reply.json().get("data", []):
        if not isinstance(row.get("id"), str) or row.get("active") is False:
            continue
        out.append(Model(id=row["id"], provider="groq",
                         context=int(row.get("context_window") or 0),
                         name=str(row.get("id"))))
    return out


_ROSTERS = {"gemini": _gemini_roster, "openrouter": _openrouter_roster,
            "groq": _groq_roster}


def roster(provider: str) -> list[Model]:
    """One provider's usable models, shape-filtered and best first.

    A PROVIDER THAT FAILS RETURNS NOTHING RATHER THAN RAISING. Losing one
    column is much better than losing the chain — and it is the caller, which
    can see all three came back empty, that is in a position to say so.
    """
    key = _key(provider)
    if key is None:
        return []
    try:
        rows = _ROSTERS[provider](key)
    except (httpx.HTTPError, ValueError, KeyError):
        return []

    rows = [m for m in rows
            if not NOT_AN_ANALYST.search(f"{m.id} {m.name}")
            and m.context >= MIN_CONTEXT]
    # WITHIN A COLUMN, WIDEST CONTEXT FIRST, then id descending so a newer
    # version number outranks an older one at equal width. A crude proxy for
    # "better" and stated as such — the real ranking is the chain, which tries
    # them in turn, so a wrong guess here costs one extra hop and nothing more.
    #
    # EXCEPT THE PRO MODELS, WHICH GO LAST. Gemini lists Pro on a free key and
    # Pro answers 429 `limit: 0` to it forever — the free tier grants that
    # model no quota at all. It has the widest context on the roster, so left
    # alone it sorts to the top of the column and burns a round trip on every
    # single fallback. Demoted rather than filtered: the day this account is
    # paid, Pro starts working and the only cost of it being last is one hop.
    rows.sort(key=lambda m: (bool(re.search(r"\bpro\b|-pro", m.id, re.I)),
                             -m.context, [-ord(c) for c in m.id]))
    return rows


def chain(preferred: str | None = None) -> list[Model]:
    """The fallback order: the pick, then one rung from each column in turn.

    ROUND-ROBIN ACROSS PROVIDERS RATHER THAN DOWN ONE. If Gemini is rate
    limiting the key, every Gemini model is rate limited — walking that column
    to the bottom is a dozen calls that all fail for the same reason before
    the first one that could have worked. Rotating means the SECOND attempt is
    already at a different provider.

    `preferred` is an exact model id when the caller pinned one, and otherwise
    the newest id matching `PREFERRED` is promoted to the head.
    """
    columns = {p: roster(p) for p in PROVIDERS}
    ordered: list[Model] = []

    head: Model | None = None
    everything = [m for p in PROVIDERS for m in columns[p]]
    if preferred:
        head = next((m for m in everything if m.id == preferred), None)
    if head is None:
        # Newest first: the ids sort such that 3.8 follows 3.7 lexically, and
        # where they do not the chain's next rung covers it.
        flashes = sorted((m for m in columns["gemini"] if PREFERRED.search(m.id)),
                         key=lambda m: m.id, reverse=True)
        head = flashes[0] if flashes else None
    if head is not None:
        ordered.append(head)

    depth = max((len(c) for c in columns.values()), default=0)
    for rung in range(depth):
        for provider in PROVIDERS:
            column = columns[provider]
            if rung < len(column) and column[rung].id != (
                    head.id if head else None):
                ordered.append(column[rung])
    return ordered


# ── asking ───────────────────────────────────────────────────────────────────

def _post(model: Model, system: str, user: str, key: str,
          effort: str | None) -> tuple[str, str]:
    """One request. Returns (text, "") or ("", why it declined)."""
    try:
        reply = httpx.post(
            ENDPOINTS[model.provider]["chat"],
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
            json={
                "model": model.id,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}],
                # Low but not zero. Zero makes every briefing of a quiet hour
                # read as a near-copy of the last one, which hides real
                # changes inside identical wording; this is warm enough to
                # vary a sentence and far too cold to invent a number.
                "temperature": 0.3,
                "max_tokens": MAX_TOKENS,
                **_effort_body(model.provider, effort),
            },
            timeout=TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        # A timeout or a dropped connection: the least reliable signal there
        # is, and never a reason to conclude anything about the model.
        return "", f"{type(exc).__name__}"

    return _read(reply)


def _ask(model: Model, system: str, user: str, key: str,
         effort: str | None = None) -> tuple[str, str]:
    """One model, one question, with the effort retried away if refused.

    NEVER RAISES FOR A REFUSAL, because a refusal is an ordinary event in a
    chain — it is the caller's loop that decides whether the chain is
    exhausted. The reason string is what gets printed beside the briefing.

    THE RETRY IS WHY THERE IS NO TABLE OF ACCEPTED EFFORTS. Every provider
    names the field in its rejection — "Invalid reasoning_effort: max",
    "`reasoning_effort` must be one of `none` or `default`" — so a model that
    will not take the level it was sent is asked again without one, and
    answers. Without this a 400 would mark the model permanently dead for the
    run and the chain would walk past a model that works perfectly well,
    every time, because of a setting the reader chose.
    """
    text, why = _post(model, system, user, key, effort)
    if text or not effort or not EFFORT_REJECTED.search(why):
        return text, why
    text, second = _post(model, system, user, key, None)
    # The FIRST reason is the one worth reporting when both failed: it says
    # the effort was refused, which is the fact that explains the retry.
    return text, "" if text else f"{why} (retried without effort: {second})"


def _read(reply: httpx.Response) -> tuple[str, str]:
    """One provider's answer, as (text, "") or ("", why it declined).

    Split out from `_ask` because there are six distinct ways to be refused
    and only one to succeed. Keeping them beside the network call made a
    function whose shape said "this mostly sends a request", when what it
    mostly does is tell six failures apart.
    """
    try:
        payload = reply.json()
    except ValueError:
        return "", f"HTTP {reply.status_code}, unreadable body"

    # A RATE-LIMITED PROVIDER CAN ANSWER HTTP 200 with an error envelope and no
    # completion at all. The envelope is the real status, so it is preferred
    # over the status line whenever both are present.
    failure = _envelope(payload)
    if failure is not None:
        said = str(failure.get("message", ""))
        code = failure.get("code")
        status = code if isinstance(code, int) else reply.status_code
        verdict = "dead" if _permanent(status, said) else "busy"
        return "", f"{status} {verdict}: {said[:120]}"
    if reply.status_code != 200:
        return "", f"HTTP {reply.status_code}: {reply.text[:120]}"

    try:
        text = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return "", "no completion in the response"
    cleaned = _strip_reasoning(text)
    if not cleaned:
        # Either the model returned nothing at all, or it spent its whole
        # budget thinking and the briefing never arrived. Both are refusals,
        # and the chain moves on.
        return "", "no briefing in the completion"
    return cleaned, ""


def _strip_reasoning(text: str | None) -> str:
    """The answer with any visible deliberation removed.

    THE WORKING-OUT IS NOT THE ANSWER. Some open reasoning models put their
    deliberation in the content itself, wrapped in `<think>` tags, rather than
    in a field of their own. Met on the first live run: Groq's qwen3.6 opened
    a briefing with "Here's a thinking process:" and several hundred words of
    it. Left in, that is not merely untidy — the reasoning contains readings
    the model then discarded and numbers it decided against, and a reader
    skimming the top of the output would take them for its conclusions.

    An UNCLOSED tag is handled too, and differently from how it looks: it
    means the model ran out of budget mid-thought, so everything after the
    opener is deliberation and none of the briefing was ever written. What
    survives is nothing, which is the honest result and lets the caller fall
    through to the next model.
    """
    if not text:
        return ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    return re.sub(r"<think>.*", "", text, flags=re.DOTALL).strip()


def complete(system: str, user: str, *,
             preferred: str | None = None,
             effort: str | None = None,
             max_attempts: int = 6) -> Completion:
    """Walk the chain until a model answers.

    `max_attempts` is a stop, not a target. The chain can be sixty models
    long and a briefing that spent four minutes finding the sixtieth is a
    briefing that arrived after the moment it described. Six is roughly two
    passes across three providers, which is enough to survive one of them
    having a bad minute and not enough to survive all three being down —
    which is the correct behaviour, because then there is nothing to say.
    """
    rungs = chain(preferred)
    if not rungs:
        held = [p for p in PROVIDERS if _key(p)]
        raise LLMError(
            "no usable model. " + (
                f"Keys are present for {', '.join(held)} but every roster came "
                "back empty or unreachable."
                if held else
                f"No key is set — expected one of "
                f"{', '.join(KEY_NAMES.values())} in the environment."))

    attempts: list[str] = []
    for model in rungs[:max_attempts]:
        key = _key(model.provider)
        if key is None:
            continue
        text, why = _ask(model, system, user, key, effort)
        if text:
            return Completion(text=text, provider=model.provider,
                              model=model.id, attempts=attempts)
        attempts.append(f"{model.provider}/{model.id} — {why}")

    raise LLMError("every model in the chain declined:\n  "
                   + "\n  ".join(attempts))
