"""services/telegram.py — splitting a briefing, and never breaking the daemon.

TWO THINGS CAN GO WRONG HERE AND ONLY ONE IS VISIBLE. A message that fails to
send is noticed the first day it happens. A message that is silently truncated
at 4,096 characters is not: the briefing arrives, reads normally, and is
simply missing its conclusion — which is the FORECAST, the part the whole
scoring experiment rests on. Most of this file is about that.

THE OTHER HALF IS ABOUT NOT MATTERING. A phone notification is the least
important thing this project does, so no failure here may reach the daemon.
Every test that passes a broken transport is checking that a briefing still
gets computed, logged and scored when Telegram is having a bad day.
"""
from __future__ import annotations

import httpx
import pytest

from integrations import telegram


@pytest.fixture(autouse=True)
def _no_ambient_config(monkeypatch):
    """A real token in .env must not leak into a test run.

    Without this, running the suite on the machine that has the bot
    configured would post test messages to the actual channel.
    """
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_REPORT_CHAT_ID", raising=False)


class TestSplit:

    def test_a_short_briefing_is_one_message(self):
        assert telegram.split("WHAT CHANGED\nnothing much") == [
            "WHAT CHANGED\nnothing much"]

    def test_nothing_is_lost_across_the_split(self):
        """THE ONE PROPERTY THAT MATTERS. A dropped chunk is a briefing that
        reads as complete and is not."""
        text = "\n\n".join(f"paragraph {i} " + "x" * 200 for i in range(40))
        # COMPARED WITH ALL WHITESPACE REMOVED FROM BOTH SIDES. The split
        # trims its cut points, so the blank line between two paragraphs is
        # legitimately absent once they are in separate messages — what must
        # survive is every character that carries meaning, and nothing else.
        squash = str.maketrans("", "", " \n\t")
        rejoined = "".join(telegram.split(text)).translate(squash)
        assert rejoined == text.translate(squash)

    def test_every_chunk_fits_telegrams_limit(self):
        text = "\n\n".join("y" * 500 for _ in range(40))
        assert all(len(c) <= telegram.MAX_MESSAGE
                   for c in telegram.split(text))

    def test_it_prefers_to_break_between_paragraphs(self):
        """A briefing cut mid-sentence across two phone notifications is much
        harder to read than one cut between its sections — and the sections
        are exactly what the prompt asks the model to write."""
        text = "\n\n".join("z" * 1000 for _ in range(6))
        chunks = telegram.split(text, limit=2500)
        assert all(not c.startswith("z" * 10) or c.count("z") % 1000 == 0
                   for c in chunks[1:])

    def test_one_enormous_paragraph_is_still_split(self):
        """No newline anywhere to break on. Cutting mid-text is ugly and is
        the only alternative to a message Telegram refuses outright."""
        chunks = telegram.split("q" * 12_000)
        assert len(chunks) > 1
        assert all(len(c) <= telegram.MAX_MESSAGE for c in chunks)
        assert "".join(chunks) == "q" * 12_000

    def test_a_single_newline_is_used_when_there_is_no_blank_line(self):
        text = "\n".join("w" * 400 for _ in range(20))
        chunks = telegram.split(text, limit=1000)
        assert len(chunks) > 1
        assert all(len(c) <= 1000 for c in chunks)


class TestConfigured:
    """Both halves are needed, and a half-configured install must say so
    rather than failing once per slot for the rest of the day."""

    def test_neither_set(self):
        assert telegram.configured() is False

    def test_token_only(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
        assert telegram.configured() is False

    def test_chat_only(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_REPORT_CHAT_ID", "-1001234")
        assert telegram.configured() is False

    def test_both(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
        monkeypatch.setenv("TELEGRAM_REPORT_CHAT_ID", "-1001234")
        assert telegram.configured() is True

    def test_whitespace_is_not_configuration(self, monkeypatch):
        """A trailing space after `=` in .env is invisible in an editor and
        would otherwise read as a configured bot that cannot post."""
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "   ")
        monkeypatch.setenv("TELEGRAM_REPORT_CHAT_ID", "-1001234")
        assert telegram.configured() is False


class TestSendNeverRaises:
    """THE DAEMON MUST SURVIVE EVERY ONE OF THESE. A briefing is computed,
    logged and scored before the message is attempted; nothing below may
    interrupt that."""

    @pytest.fixture
    def _configured(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
        monkeypatch.setenv("TELEGRAM_REPORT_CHAT_ID", "-1001234")

    def test_unconfigured_is_false_not_an_exception(self):
        assert telegram.send("anything") is False

    def test_a_network_failure_is_false(self, _configured, monkeypatch):
        def boom(*a, **k):
            raise httpx.ConnectError("no route")
        monkeypatch.setattr(telegram.httpx, "post", boom)
        assert telegram.send("hello") is False

    def test_a_rejected_message_is_false(self, _configured, monkeypatch):
        monkeypatch.setattr(telegram.httpx, "post", lambda *a, **k:
                            httpx.Response(400, json={"description": "bad"},
                                           request=httpx.Request("POST", "http://x")))
        assert telegram.send("hello") is False

    def test_a_good_send_is_true(self, _configured, monkeypatch):
        monkeypatch.setattr(telegram.httpx, "post", lambda *a, **k:
                            httpx.Response(200, json={"ok": True},
                                           request=httpx.Request("POST", "http://x")))
        assert telegram.send("hello") is True

    def test_a_long_briefing_sends_every_chunk(self, _configured, monkeypatch):
        sent = []

        def record(*a, **kwargs):
            sent.append(kwargs["json"]["text"])
            return httpx.Response(200, json={"ok": True},
                                  request=httpx.Request("POST", "http://x"))
        monkeypatch.setattr(telegram.httpx, "post", record)

        text = "\n\n".join("p" * 900 for _ in range(8))
        assert telegram.send(text) is True
        assert len(sent) > 1

    def test_partial_delivery_reports_failure(self, _configured, monkeypatch):
        """Three chunks of four arriving is not success. A briefing missing
        its conclusion has lost the part the reader wanted, and returning True
        would hide it."""
        replies = iter([200, 200, 500, 200])

        def flaky(*a, **k):
            return httpx.Response(next(replies), json={},
                                  request=httpx.Request("POST", "http://x"))
        monkeypatch.setattr(telegram.httpx, "post", flaky)

        text = "\n\n".join("p" * 1200 for _ in range(8))
        assert telegram.send(text) is False


class TestDiscoveringChats:
    """`getUpdates` is the only way to learn a chat id — Telegram does not
    publish them. These pin the shapes, because a channel and a private
    message carry the chat under different keys and getting it wrong means
    setup silently finds nothing."""

    @pytest.fixture(autouse=True)
    def _token(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")

    def _updates(self, monkeypatch, payload):
        monkeypatch.setattr(telegram.httpx, "get", lambda *a, **k:
                            httpx.Response(200, json={"result": payload},
                                           request=httpx.Request("GET", "http://x")))

    def test_a_channel_post_is_found(self, monkeypatch):
        self._updates(monkeypatch, [{"channel_post": {
            "chat": {"id": -1001234567890, "type": "channel",
                     "title": "SPX Reports"}}}])
        assert telegram.recent_chats()[0]["title"] == "SPX Reports"

    def test_a_private_message_is_found(self, monkeypatch):
        self._updates(monkeypatch, [{"message": {
            "chat": {"id": 12345, "type": "private", "first_name": "C"}}}])
        assert telegram.recent_chats()[0]["id"] == 12345

    def test_one_chat_is_listed_once(self, monkeypatch):
        """Ten messages in one channel is one destination, not ten."""
        self._updates(monkeypatch, [
            {"channel_post": {"chat": {"id": -1001, "title": "A"}}}
            for _ in range(10)])
        assert len(telegram.recent_chats()) == 1

    def test_an_empty_queue_is_not_an_error(self, monkeypatch):
        """The normal state of a bot nobody has written to — and also what a
        working bot returns the SECOND time setup is run, because getUpdates
        is a queue that empties when read."""
        self._updates(monkeypatch, [])
        assert telegram.recent_chats() == []

    def test_a_network_failure_returns_no_chats(self, monkeypatch):
        def boom(*a, **k):
            raise httpx.ReadTimeout("slow")
        monkeypatch.setattr(telegram.httpx, "get", boom)
        assert telegram.recent_chats() == []
