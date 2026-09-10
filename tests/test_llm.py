"""services/llm.py — the roster filter, the fallback chain, and the refusals.

WHY THIS FILE EXISTS AT ALL. Reading Buddy, whose pattern this ports, keeps
its equivalent judgements under test for a reason it states plainly: the
decision about which models are fit is a judgement, and it is wrong often
enough to need one. Everything here is that judgement or the classification
that feeds it.

WHAT IS NOT TESTED, DELIBERATELY. No test asks whether a briefing is any
good, or whether one model reads a chain better than another. Nothing in a
test file can know that, and a test that pretended to would pin a preference
rather than a fact. The scoring log (`scripts/score_briefings.py`) is where
that question is answered, over weeks, against the close.

THE PAYLOADS ARE REAL. Every fixture below is the shape a provider actually
returned during the first live run on 2026-09-08 — Gemini's array-wrapped
error envelope, OpenRouter's 403 on a gated "free" model, Groq's `<think>`
block. They were met, not imagined, and that is the whole value of them: the
next person to read this file learns which failures happen.
"""
from __future__ import annotations

import httpx
import pytest

from integrations import llm


def _response(body, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=body,
                          request=httpx.Request("POST", "http://example.invalid"))


def _completion(text) -> httpx.Response:
    return _response({"choices": [{"message": {"content": text}}]})


# ── telling a dead model from a busy one ─────────────────────────────────────

class TestRefusals:
    """The rule the whole chain rests on: which failures are worth retrying.

    Getting this backwards is expensive in both directions. Treat every
    refusal as permanent and one busy minute deletes a good model for the
    rest of the day; treat every refusal as temporary and the chain spends
    its whole budget re-asking a model that was retired last month.
    """

    def test_gate_and_retirement_are_permanent(self):
        # 403 is the gated case met live: OpenRouter lists a model as free and
        # then declines it because it is "only available on agentic harnesses".
        assert llm._permanent(403, "only available on agentic harnesses")
        assert llm._permanent(404, "no such model")
        assert llm._permanent(400, "unsupported")

    def test_a_zero_quota_429_is_permanent(self):
        # GEMINI REPORTS TWO DIFFERENT THINGS AS 429 and the quota it names is
        # the only thing separating them. Zero means there was never any quota
        # to use up — this is how Pro answers a free key, forever.
        assert llm._permanent(429, "quota exceeded, limit: 0 for this model")

    def test_an_ordinary_429_is_not_permanent(self):
        # A real "you are going too fast" names a limit above zero. Deleting on
        # this would throw the model away for being popular.
        assert not llm._permanent(429, "rate limit exceeded, limit: 60 per min")

    def test_high_demand_is_busy(self):
        # The most common refusal of the first live run, and the one that must
        # never delete a model: 3.8-flash answered this at 09:45 and answered
        # the briefing normally fifteen minutes later.
        assert not llm._permanent(503, "This model is currently experiencing "
                                       "high demand.")


class TestErrorEnvelope:
    """Gemini wraps its error in a LIST. Everyone else wraps it in an object.

    Reading only the object shape loses Gemini's message entirely — and
    `limit: 0` lives in that message, so the zero-quota rule above would
    silently stop working for the one provider it was written for.
    """

    def test_the_object_shape(self):
        assert llm._envelope({"error": {"code": 503}}) == {"code": 503}

    def test_geminis_array_shape(self):
        assert llm._envelope([{"error": {"code": 429}}]) == {"code": 429}

    def test_a_good_answer_has_no_envelope(self):
        assert llm._envelope({"choices": [{"message": {"content": "hi"}}]}) is None

    def test_an_empty_array_is_not_an_error(self):
        assert llm._envelope([]) is None


# ── reading one provider's answer ────────────────────────────────────────────

class TestReadingTheAnswer:

    def test_a_plain_answer_comes_back_whole(self):
        assert llm._read(_completion("WHAT CHANGED\nthe day turned")) == (
            "WHAT CHANGED\nthe day turned", "")

    def test_reasoning_is_stripped_from_the_briefing(self):
        """Met live: Groq's qwen3.6 wrote its deliberation into the answer.

        This is not tidiness. The reasoning contains readings the model then
        DISCARDED and numbers it decided against — 7,999 below is a level it
        rejected — and a reader skimming the top of the output would take
        them for the briefing's conclusions.
        """
        text, why = llm._read(_completion(
            "<think>maybe 7,999? no, discard that</think>\nCALL: no pin today"))
        assert why == ""
        assert text == "CALL: no pin today"
        assert "7,999" not in text

    def test_an_unclosed_think_block_is_a_refusal(self):
        """It means the budget ran out mid-thought and no briefing was written.

        Everything after the opener is deliberation, so the honest result is
        nothing — and nothing must send the chain to the next model rather
        than printing half a thought under a briefing's heading.
        """
        assert llm._read(_completion("<think>still working it out")) == (
            "", "no briefing in the completion")

    def test_a_null_completion_is_a_refusal(self):
        assert llm._read(_completion(None))[0] == ""

    def test_an_error_envelope_beats_a_200_status(self):
        """A rate-limited provider can answer HTTP 200 with an error and no
        completion at all. The envelope is the real status."""
        _, why = llm._read(_response([{"error": {"code": 429,
                                                 "message": "limit: 0"}}], 200))
        assert "429 dead" in why

    def test_a_busy_refusal_says_busy(self):
        _, why = llm._read(_response({"error": {"code": 503,
                                                "message": "high demand"}}, 503))
        assert "503 busy" in why

    def test_an_unreadable_body_does_not_raise(self):
        reply = httpx.Response(
            502, text="<html>bad gateway</html>",
            request=httpx.Request("POST", "http://example.invalid"))
        text, why = llm._read(reply)
        assert text == ""
        assert "502" in why


class TestStripReasoning:

    def test_it_survives_no_tags(self):
        assert llm._strip_reasoning("plain") == "plain"

    def test_it_survives_none(self):
        assert llm._strip_reasoning(None) == ""

    def test_several_blocks_all_go(self):
        assert llm._strip_reasoning("<think>a</think>X<think>b</think>Y") == "XY"


# ── which models are offered ─────────────────────────────────────────────────

def _roster_reply(monkeypatch, payload):
    """Point every roster fetch at one canned payload."""
    monkeypatch.setattr(llm.httpx, "get",
                        lambda *a, **k: _response(payload))


class TestRosterFiltering:

    @pytest.fixture(autouse=True)
    def _key(self, monkeypatch):
        for name in llm.KEY_NAMES.values():
            monkeypatch.setenv(name, "test-key")

    def test_only_free_openrouter_models_are_offered(self, monkeypatch):
        """THE ONE PROVIDER WHERE THE ROSTER CAN ANSWER THE FREE QUESTION.

        Asked strictly, because this is the only thing standing between the
        chain and a bill. A model priced at anything at all is not offered.
        """
        _roster_reply(monkeypatch, {"data": [
            {"id": "free/one", "context_length": 100_000,
             "pricing": {"prompt": "0", "completion": "0"}},
            {"id": "paid/two", "context_length": 100_000,
             "pricing": {"prompt": "0.0000001", "completion": "0"}},
        ]})
        ids = [m.id for m in llm.roster("openrouter")]
        assert ids == ["free/one"]

    def test_unreadable_pricing_is_not_treated_as_free(self, monkeypatch):
        """Absent or malformed pricing means unknown, and unknown is not free.

        Guessing the safe way round costs one model. Guessing the other way
        spends money the account did not agree to spend.
        """
        _roster_reply(monkeypatch, {"data": [
            {"id": "mystery/one", "context_length": 100_000},
            {"id": "odd/two", "context_length": 100_000,
             "pricing": {"prompt": "free", "completion": "free"}},
        ]})
        assert llm.roster("openrouter") == []

    def test_narrow_models_are_dropped(self, monkeypatch):
        """A classifier does not FAIL at reading a dashboard — it answers
        confidently in the wrong genre, which nothing downstream can detect."""
        _roster_reply(monkeypatch, {"data": [
            {"id": "vendor/general-70b", "context_length": 100_000,
             "pricing": {"prompt": "0", "completion": "0"}},
            {"id": "vendor/content-safety", "context_length": 100_000,
             "pricing": {"prompt": "0", "completion": "0"}},
            {"id": "vendor/text-embedding", "context_length": 100_000,
             "pricing": {"prompt": "0", "completion": "0"}},
            {"id": "vendor/whisper-large", "context_length": 100_000,
             "pricing": {"prompt": "0", "completion": "0"}},
        ]})
        assert [m.id for m in llm.roster("openrouter")] == ["vendor/general-70b"]

    def test_short_context_models_are_dropped(self, monkeypatch):
        """A model that cannot hold the whole prompt drops the TOP of it,
        which here means losing the volume rung and answering from gamma
        alone — without saying so."""
        _roster_reply(monkeypatch, {"data": [
            {"id": "big/one", "context_length": llm.MIN_CONTEXT,
             "pricing": {"prompt": "0", "completion": "0"}},
            {"id": "small/two", "context_length": llm.MIN_CONTEXT - 1,
             "pricing": {"prompt": "0", "completion": "0"}},
        ]})
        assert [m.id for m in llm.roster("openrouter")] == ["big/one"]

    def test_gemini_needs_generatecontent(self, monkeypatch):
        """The image, speech and embedding models list something else."""
        _roster_reply(monkeypatch, {"models": [
            {"name": "models/gemini-3.8-flash", "inputTokenLimit": 1_000_000,
             "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/text-embedding-004", "inputTokenLimit": 1_000_000,
             "supportedGenerationMethods": ["embedContent"]},
        ]})
        assert [m.id for m in llm.roster("gemini")] == ["gemini-3.8-flash"]

    def test_the_models_prefix_is_stripped(self, monkeypatch):
        """The OpenAI-compatible endpoint the chain posts to does not take it,
        so leaving it on makes every Gemini rung a 404."""
        _roster_reply(monkeypatch, {"models": [
            {"name": "models/gemini-3.8-flash", "inputTokenLimit": 1_000_000,
             "supportedGenerationMethods": ["generateContent"]}]})
        assert llm.roster("gemini")[0].id == "gemini-3.8-flash"

    def test_pro_sorts_last_even_though_it_is_widest(self, monkeypatch):
        """Gemini lists Pro on a free key and Pro answers `limit: 0` forever.

        It has the widest context on the roster, so left alone it sorts to the
        top of the column and burns a round trip on EVERY fallback. Demoted
        rather than filtered: the day the account is paid it starts working.
        """
        _roster_reply(monkeypatch, {"models": [
            {"name": f"models/{slug}", "inputTokenLimit": 1_000_000,
             "supportedGenerationMethods": ["generateContent"]}
            for slug in ("gemini-pro-latest", "gemini-3.6-flash",
                         "gemini-3.8-flash")]})
        assert [m.id for m in llm.roster("gemini")] == [
            "gemini-3.8-flash", "gemini-3.6-flash", "gemini-pro-latest"]

    def test_a_provider_with_no_key_contributes_nothing(self, monkeypatch):
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        assert llm.roster("groq") == []

    def test_a_failed_roster_loses_its_column_not_the_chain(self, monkeypatch):
        """Losing one column is much better than losing the picker."""
        def boom(*a, **k):
            raise httpx.ConnectError("no route")
        monkeypatch.setattr(llm.httpx, "get", boom)
        assert llm.roster("openrouter") == []


# ── the order they are tried in ──────────────────────────────────────────────

def _fake_rosters(monkeypatch, columns: dict[str, list[str]]):
    """Replace `roster` wholesale — the chain's job is ORDER, not filtering."""
    def fake(provider):
        return [llm.Model(id=slug, provider=provider, context=100_000)
                for slug in columns.get(provider, [])]
    monkeypatch.setattr(llm, "roster", fake)


class TestChain:

    def test_the_newest_flash_leads(self, monkeypatch):
        """Chandan's stated preference, matched against the LIVE roster rather
        than pinned as an id — the version number moves, and a hardcoded
        `gemini-3.8-flash` would 404 the week Google renames it."""
        _fake_rosters(monkeypatch, {"gemini": ["gemini-3.6-flash",
                                               "gemini-3.8-flash"]})
        assert llm.chain()[0].id == "gemini-3.8-flash"

    def test_an_explicit_pick_leads_instead(self, monkeypatch):
        _fake_rosters(monkeypatch, {"gemini": ["gemini-3.8-flash"],
                                    "groq": ["qwen/qwen3.8-27b"]})
        assert llm.chain("qwen/qwen3.8-27b")[0].id == "qwen/qwen3.8-27b"

    def test_the_head_is_never_repeated(self, monkeypatch):
        """A chain that retries its own head wastes the one attempt most
        likely to fail for the same reason it just failed."""
        _fake_rosters(monkeypatch, {"gemini": ["gemini-3.8-flash", "b"],
                                    "groq": ["c"]})
        ids = [m.id for m in llm.chain()]
        assert ids.count("gemini-3.8-flash") == 1

    def test_the_second_attempt_is_a_different_provider(self, monkeypatch):
        """THE POINT OF ROTATING RATHER THAN WALKING ONE COLUMN. If Gemini is
        rate limiting the key then EVERY Gemini model is, so a column walked
        top to bottom is a dozen calls that all fail for one reason before the
        first one that could have worked."""
        _fake_rosters(monkeypatch, {
            "gemini": ["gemini-3.8-flash", "gemini-flash-lite-latest"],
            "openrouter": ["or/one"], "groq": ["groq/one"]})
        chain = llm.chain()
        assert chain[0].provider == "gemini"
        assert chain[1].provider != "gemini"

    def test_every_model_appears(self, monkeypatch):
        """A rung dropped by the rotation is a model that can never answer,
        and nothing would ever report it missing."""
        _fake_rosters(monkeypatch, {"gemini": ["g1", "g2", "g3"],
                                    "openrouter": ["o1"], "groq": ["q1", "q2"]})
        assert sorted(m.id for m in llm.chain()) == [
            "g1", "g2", "g3", "o1", "q1", "q2"]

    def test_an_uneven_roster_does_not_lose_the_deep_column(self, monkeypatch):
        _fake_rosters(monkeypatch, {"gemini": ["g1"],
                                    "openrouter": ["o1", "o2", "o3", "o4"]})
        assert len(llm.chain()) == 5

    def test_no_rosters_is_an_empty_chain(self, monkeypatch):
        _fake_rosters(monkeypatch, {})
        assert llm.chain() == []


# ── the loop ─────────────────────────────────────────────────────────────────

class TestComplete:

    @pytest.fixture(autouse=True)
    def _keys(self, monkeypatch):
        for name in llm.KEY_NAMES.values():
            monkeypatch.setenv(name, "test-key")

    def test_it_steps_past_a_busy_model(self, monkeypatch):
        """The failure of the first live run, reproduced: 3.8-flash answered
        503 at 09:45 and the briefing came from the next rung."""
        _fake_rosters(monkeypatch, {"gemini": ["gemini-3.8-flash", "lite"]})
        replies = iter([
            _response({"error": {"code": 503, "message": "high demand"}}, 503),
            _completion("CALL: no pin today"),
        ])
        monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: next(replies))

        answer = llm.complete("system", "user")
        assert answer.text == "CALL: no pin today"
        assert answer.model == "lite"
        # THE SKIPPED RUNGS TRAVEL WITH THE ANSWER. A briefing from the fourth
        # model is a different thing from one from the first, and the reader
        # is entitled to know which they are holding.
        assert len(answer.attempts) == 1
        assert "503" in answer.attempts[0]

    def test_the_first_answer_wins(self, monkeypatch):
        _fake_rosters(monkeypatch, {"gemini": ["gemini-3.8-flash", "lite"]})
        monkeypatch.setattr(llm.httpx, "post",
                            lambda *a, **k: _completion("first"))
        answer = llm.complete("system", "user")
        assert answer.model == "gemini-3.8-flash"
        assert answer.attempts == []

    def test_max_attempts_stops_the_walk(self, monkeypatch):
        """A stop, not a target. A briefing that spent four minutes finding
        the sixtieth model arrived after the moment it described."""
        _fake_rosters(monkeypatch, {"gemini": [f"m{i}" for i in range(20)]})
        calls = []

        def refuse(*a, **k):
            calls.append(1)
            return _response({"error": {"code": 503, "message": "busy"}}, 503)
        monkeypatch.setattr(llm.httpx, "post", refuse)

        with pytest.raises(llm.LLMError):
            llm.complete("system", "user", max_attempts=3)
        assert len(calls) == 3

    def test_every_refusal_is_reported(self, monkeypatch):
        """The error names each model and what it said. A bare "it failed"
        cannot be acted on — three providers down and one bad key look the
        same from the outside."""
        _fake_rosters(monkeypatch, {"gemini": ["a"], "groq": ["b"]})
        monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: _response(
            {"error": {"code": 503, "message": "high demand"}}, 503))
        with pytest.raises(llm.LLMError, match="every model in the chain"):
            llm.complete("system", "user")

    def test_no_key_at_all_says_which_to_set(self, monkeypatch):
        for name in llm.KEY_NAMES.values():
            monkeypatch.delenv(name, raising=False)
        _fake_rosters(monkeypatch, {})
        with pytest.raises(llm.LLMError, match="GEMINI_API_KEY"):
            llm.complete("system", "user")

    def test_keys_present_but_rosters_empty_says_so_differently(
            self, monkeypatch):
        """Two failures that look identical from a terminal and need different
        fixes: nothing configured, against everything configured and the
        providers unreachable."""
        _fake_rosters(monkeypatch, {})
        with pytest.raises(llm.LLMError, match="came back empty"):
            llm.complete("system", "user")

    def test_a_network_error_is_a_refusal_not_a_crash(self, monkeypatch):
        _fake_rosters(monkeypatch, {"gemini": ["a"], "groq": ["b"]})
        replies = iter([httpx.ConnectTimeout("slow"), _completion("answer")])

        def maybe(*a, **k):
            item = next(replies)
            if isinstance(item, Exception):
                raise item
            return item
        monkeypatch.setattr(llm.httpx, "post", maybe)

        answer = llm.complete("system", "user")
        assert answer.text == "answer"
        assert "ConnectTimeout" in answer.attempts[0]


class TestReasoningEffort:
    """All three providers take an effort, under two names and on ladders that
    differ BY MODEL — measured against the live APIs on 2026-09-08:

        gemini-flash-lite-latest   minimal, low, medium, high, none
        groq openai/gpt-oss-20b    low, medium, high
        groq qwen/qwen3.6-27b      none, default
        openrouter nemotron-3.5    the whole ladder, max included

    There is deliberately no table of that in the code, because the rosters
    churn weekly and the table would be wrong within a fortnight. These tests
    pin the two things that make the table unnecessary: the spelling per
    provider, and the retry that drops the field when a model refuses it.
    """

    def test_openrouter_takes_an_object(self):
        assert llm._effort_body("openrouter", "high") == {
            "reasoning": {"effort": "high", "exclude": False}}

    def test_the_others_take_the_bare_word(self):
        assert llm._effort_body("gemini", "low") == {"reasoning_effort": "low"}
        assert llm._effort_body("groq", "low") == {"reasoning_effort": "low"}

    def test_max_is_capped_for_the_short_ladders(self):
        """Gemini answered 400 to `max` naming its valid values; sending it
        anyway would have marked a working model dead for the whole run."""
        assert llm._effort_body("gemini", "max") == {"reasoning_effort": "high"}
        assert llm._effort_body("groq", "xhigh") == {"reasoning_effort": "high"}

    def test_openrouter_keeps_the_top_of_the_ladder(self):
        """It was measured accepting `max`, so capping it there would throw
        away the only provider that offers the level the reader asked for."""
        assert llm._effort_body("openrouter", "max")["reasoning"]["effort"] == "max"

    def test_no_effort_sends_no_field(self):
        assert llm._effort_body("gemini", None) == {}

    @pytest.mark.parametrize("message", [
        "Invalid reasoning_effort: max. Valid values are: high, low, medium",
        "`reasoning_effort` must be one of `none` or `default`",
        "`reasoning_effort` must be one of `low`, `medium`, or `high`",
    ])
    def test_the_real_refusals_are_recognised(self, message):
        """Verbatim from the three providers. The retry hangs off matching
        these, so a reworded regex that stopped matching would silently take
        every effort-refusing model out of the chain."""
        assert llm.EFFORT_REJECTED.search(message)

    def test_an_unrelated_refusal_is_not_retried(self):
        """A rate limit re-asked without an effort is one wasted call against
        a tier that is already refusing."""
        assert not llm.EFFORT_REJECTED.search("429: quota exceeded")

    def test_a_refused_effort_is_asked_again_without_one(self, monkeypatch):
        sent = []

        def fake(model, system, user, key, effort):
            sent.append(effort)
            if effort is not None:
                return "", "400 dead: `reasoning_effort` must be one of `none`"
            return "the answer", ""
        monkeypatch.setattr(llm, "_post", fake)
        model = llm.Model(id="m", provider="groq", context=1, name="m")
        text, why = llm._ask(model, "sys", "user", "key", "high")
        assert text == "the answer"
        assert why == ""
        assert sent == ["high", None]

    def test_a_model_that_takes_it_is_asked_once(self, monkeypatch):
        calls = []
        monkeypatch.setattr(llm, "_post",
                            lambda *a: (calls.append(a) or ("fine", "")))
        model = llm.Model(id="m", provider="gemini", context=1, name="m")
        assert llm._ask(model, "s", "u", "k", "high")[0] == "fine"
        assert len(calls) == 1

    def test_both_failing_reports_the_first_reason(self, monkeypatch):
        """The refusal of the effort is what explains the retry; the second
        reason alone would read as though the effort had never been the
        problem."""
        monkeypatch.setattr(llm, "_post", lambda m, s, u, k, e: (
            ("", "400 dead: `reasoning_effort` must be one of `none`")
            if e else ("", "503 busy")))
        model = llm.Model(id="m", provider="groq", context=1, name="m")
        _, why = llm._ask(model, "s", "u", "k", "high")
        assert "reasoning_effort" in why
        assert "503 busy" in why
