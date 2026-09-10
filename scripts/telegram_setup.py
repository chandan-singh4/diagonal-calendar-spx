"""
telegram_setup.py — prove the bot works, and find the chat id to post to.

    python -m scripts.telegram_setup            # check the token, list chats
    python -m scripts.telegram_setup --test     # send a test message

WHY A SCRIPT AND NOT A PARAGRAPH OF INSTRUCTIONS. Three things can be wrong
when a briefing does not arrive — the token, the chat id, or the bot not
having been added to the channel — and they all look identical from the
outside: silence. This tells them apart one at a time, in the order they have
to be fixed in.

WHAT TO DO, ONCE:

  1. In Telegram, message @BotFather and send /newbot. It asks for a name and
     a username, then hands back a token that looks like
     `1234567890:AAF...`. Put it in .env as TELEGRAM_BOT_TOKEN.

  2. Run this script with no arguments. It should print the bot's name. If it
     does not, the token is wrong and nothing else matters yet.

  3. Make the channel the reports go to. Add the bot to it as an
     ADMINISTRATOR — a plain member cannot post. Then send any message in
     that channel.

  4. Run this script again. It prints the chat id, which for a channel is
     negative and starts -100. Put it in .env as TELEGRAM_REPORT_CHAT_ID.

  5. Run with --test. If the message arrives, the daemon will post there too.

THE CHAT ID CANNOT BE LOOKED UP. Telegram does not publish it; a chat reveals
its id only when something is sent from it. That is why step 3 says to send a
message and is not an optional tidiness step.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: F401  — imported for its .env load, nothing else
from integrations import telegram


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test", action="store_true",
                        help="send a test message to TELEGRAM_REPORT_CHAT_ID")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if telegram.token() is None:
        print("TELEGRAM_BOT_TOKEN is not set in .env.")
        print("Message @BotFather in Telegram, send /newbot, and paste the "
              "token it gives you.")
        return 1

    me = telegram.whoami()
    if me is None:
        # THE TOKEN IS THE FIRST THING AND THE ONLY THING AT THIS POINT. Every
        # later failure is indistinguishable from this one, so it is worth
        # stopping here rather than reporting three possible causes at once.
        print("The token was rejected. Check TELEGRAM_BOT_TOKEN in .env — it "
              "should look like 1234567890:AAF... with no quotes.")
        return 1
    print(f"Bot: {me.get('first_name')} (@{me.get('username')})")

    chat = telegram.report_chat()
    print(f"TELEGRAM_REPORT_CHAT_ID: {chat or 'not set'}")

    chats = telegram.recent_chats()
    if chats:
        print("\nChats that have messaged this bot recently:")
        for row in chats:
            title = row.get("title") or row.get("first_name") or "(no title)"
            print(f"  {row['id']:<16} {row.get('type', ''):<10} {title}")
        print("\nA channel's id is negative and starts -100.")
    else:
        # NOT AN ERROR, and worth saying so: `getUpdates` is a queue, so this
        # is also what a working bot prints the second time it is run.
        print("\nNo recent messages. Send something in the channel (and make "
              "sure the bot is an ADMINISTRATOR there), then run this again. "
              "Note this list empties once read.")

    if args.test:
        if chat is None:
            print("\nCannot send a test: TELEGRAM_REPORT_CHAT_ID is not set.")
            return 1
        stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        if telegram.send(f"SPX briefing bot connected — {stamp}.\n\n"
                         "This channel will receive the session briefings."):
            print("\nTest message sent.")
            return 0
        print("\nThe test message failed. The usual cause is the bot not "
              "being an administrator of that channel, or the chat id "
              "belonging to a different chat.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
