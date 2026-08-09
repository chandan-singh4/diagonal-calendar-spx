"""Is the collector still collecting? Asked from outside the dashboard.

WHY THIS EXISTS, precisely. On 2026-08-09 the Schwab token had expired and the
collector was recording nothing. The dashboard has had a full-width red
"TOKEN EXPIRED" banner for weeks (ui/header.py) and it was working perfectly —
nobody was looking at it. The markets were shut, so nothing was lost. Monday
would have cost a session, and the broker will not sell you last Tuesday's
prices.

So the rule this script is built around: **an alarm that can only reach you
through a page you have to open is not an alarm.** It runs on a schedule, it
does not need the dashboard, and it speaks through a desktop pop-up and an
email.

WHAT IT DOES NOT DO. It never writes to the database, never restarts the
collector, and never re-authenticates. It observes and it tells you. Deciding
what to do about a dead collector stays with Chandan — a watchdog that takes
action is a second thing that can go wrong unattended, and this one is meant
to be the thing you trust when everything else is suspect.

THE FOUR WAYS A NAIVE VERSION CRIES WOLF, all handled below:
  1. Overnight and at weekends the collector is idle BY DESIGN. Checked via
     core.session, which returns None for "market shut" — not zero.
  2. At 09:31 the newest price is legitimately yesterday's close.
     WATCHDOG_OPEN_GRACE_MINUTES covers the first cycle or two.
  3. Age reaches the polling interval immediately before every new price
     lands. WATCHDOG_LATE_MULTIPLE means two missed cycles, not one slow one.
  4. A five-minute schedule turns one outage into twelve emails an hour.
     WATCHDOG_REALERT_MINUTES holds it to one, plus an all-clear when it
     recovers — the all-clear matters, because silence is what a dead
     watchdog also sounds like.

USAGE
    python scripts/watchdog.py            # check once, alert if needed
    python scripts/watchdog.py --dry-run  # report only; sends nothing
    python scripts/watchdog.py --test-alert
                                          # send a pop-up and an email NOW, to
                                          # prove the alerting path works while
                                          # nothing is wrong. An untested alarm
                                          # is an assumption.

Exit codes: 0 all well (or market shut), 1 a problem was found and reported,
2 the check itself could not run. The scheduler cares; a human reads the text.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import smtplib
import subprocess
import sys
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import db
import schwab_client
from core import session as core_session

# Where the "have I already complained about this?" note lives. Beside the
# other sidecars, and gitignored with them.
STATE_PATH = Path(config.STATE_DIR) / "watchdog_state.json"


# ─────────────────────────────────────────────────────────────────────────────
# The check
# ─────────────────────────────────────────────────────────────────────────────

def check(now_utc: datetime | None = None) -> dict:  # noqa: PLR0911
    """Look at the world and describe it. Reads nothing but the database clock.

    The eight returns are the eight verdicts, and ruff's PLR0911 is waved off
    rather than obeyed: collapsing them into one exit point would mean
    accumulating a severity in a variable, and the one property this function
    must have is that each condition states its own conclusion where the
    condition is tested.


    Returns a dict with `ok` (bool), `severity` ('ok'|'warn'|'alarm'),
    `headline`, and `detail`. Deliberately returns rather than alerts, so the
    tests below can drive every branch without a mailbox or a desktop.
    """
    now_utc = now_utc or datetime.now(UTC)
    now_et = now_utc.astimezone(ZoneInfo(config.DISPLAY_TIMEZONE))

    session = core_session.session_of(now_et, config.MARKET_HOLIDAYS)
    interval = core_session.expected_interval(
        session, config.POLL_INTERVAL_EVENT, config.POLL_INTERVAL_NORMAL
    )

    # ── The market is shut ───────────────────────────────────────────────────
    if interval is None:
        return _result("ok", "Market closed — collector idle by design",
                       f"{now_et:%Y-%m-%d %H:%M} ET. Nothing is expected right now.",
                       informative=False)

    # ── Inside the opening grace period ──────────────────────────────────────
    open_today = now_et.replace(hour=core_session.OPEN_START.hour,
                                minute=core_session.OPEN_START.minute,
                                second=0, microsecond=0)
    minutes_since_open = (now_et - open_today).total_seconds() / 60
    if minutes_since_open < config.WATCHDOG_OPEN_GRACE_MINUTES:
        return _result("ok", "Just opened — waiting for the first cycle",
                       f"{minutes_since_open:.1f} min since the bell; grace is "
                       f"{config.WATCHDOG_OPEN_GRACE_MINUTES} min.",
                       informative=False)

    # ── How old is the newest price? ─────────────────────────────────────────
    try:
        snap = db.get_latest_complete_snapshot(config.DB_PATH)
    except Exception as exc:
        # The database being unreadable is itself an alarm — it is the one
        # thing this script depends on, and silence would be indistinguishable
        # from "all well".
        return _result("alarm", "Cannot read the database",
                       f"{type(exc).__name__}: {exc}")

    if snap is None:
        return _result("alarm", "No completed price snapshot exists at all",
                       "The database has no COMPLETE snapshot. If this is a new "
                       "install that is expected; otherwise the collector has "
                       "never successfully finished a cycle.")

    snap_dt = datetime.strptime(
        snap["snapshot_timestamp"][:19], "%Y-%m-%d %H:%M:%S"
    ).replace(tzinfo=UTC)
    age = (now_utc - snap_dt).total_seconds()
    limit = interval * config.WATCHDOG_LATE_MULTIPLE

    token_note = _token_note()

    # A price newer than "now" is impossible, so something is lying about the
    # time — a wrong system clock, or a timestamp written in the wrong zone.
    # Found by probing this function with a past date during M3.4, where it
    # sailed through as "collecting normally, newest price -2001584s old".
    # It must not read as healthy: every other judgement here is an arithmetic
    # comparison against a clock, so a clock that is wrong makes all of them
    # meaningless, including the reassuring ones.
    if age < -60:
        return _result(
            "alarm", "The newest price is timestamped in the FUTURE",
            f"Newest snapshot {snap['snapshot_timestamp']} UTC is {_fmt(-age)} "
            f"ahead of now ({now_utc:%Y-%m-%d %H:%M:%S} UTC).\n"
            "Every staleness check depends on the clock, so nothing this "
            "watchdog says can be trusted until that is explained.\n"
            "Check this machine's clock and timezone first.",
        )

    if age > limit:
        return _result(
            "alarm",
            f"No prices for {_fmt(age)} — collection has stopped",
            f"Session {session}: a price is expected every {interval}s, so "
            f"{_fmt(age)} is past the {_fmt(limit)} limit.\n"
            f"Newest snapshot: {snap['snapshot_timestamp']} UTC.\n"
            f"{token_note}\n"
            "Prices missed while this continues cannot be recovered later.",
        )

    if token_note.startswith("WARNING"):
        return _result("warn", "Prices are arriving, but the token is nearly out",
                       f"Newest price {_fmt(age)} old — healthy.\n{token_note}")

    return _result("ok", f"Collecting normally — newest price {_fmt(age)} old",
                   f"Session {session}, expecting one every {interval}s. {token_note}")


def _token_note() -> str:
    """One line about the Schwab token, or why we could not tell.

    Never raises: the token check is a bonus, and a watchdog that dies while
    checking a secondary thing has failed at its primary job.
    """
    try:
        age_days = schwab_client.get_token_age_days()
    except Exception as exc:
        return f"(Could not read the token age: {type(exc).__name__}.)"
    if age_days is None:
        return "(Token age unknown.)"
    remaining = 7 - age_days
    if remaining <= 0:
        return (f"WARNING: the Schwab token EXPIRED {-remaining:.1f} days ago — "
                "this is very likely the cause. Run: python scripts/reauth.py")
    if remaining <= 1:
        return (f"WARNING: the Schwab token expires in {remaining:.1f} days. "
                "Run: python scripts/reauth.py")
    return f"Token has {remaining:.1f} days left."


def _result(severity: str, headline: str, detail: str,
            informative: bool = True) -> dict:
    """`informative=False` means "I have no news", NOT "all is well".

    THE FALSE ALL-CLEAR. Chandan asked whether a ten-minute schedule meant an
    email every ten minutes; explaining why it does not exposed this. At 16:00
    the market shuts and the check starts answering "ok — market closed". If
    the collector had been dead all afternoon, that flip from alarming to ok
    fired a RECOVERED email: *prices are arriving again*. They were not. The
    market had simply closed, and the watchdog had stopped being able to tell.

    A false all-clear is the worst thing an alarm can say, because it is
    specifically the message that stops you looking. So the two states where
    the watchdog genuinely cannot see — market shut, and the grace period
    after the bell — say so, and the caller leaves the alarm exactly as it
    found it. The all-clear now requires positively observing fresh data.
    """
    return {"ok": severity == "ok", "severity": severity,
            "headline": headline, "detail": detail,
            "informative": informative}


def _fmt(secs: float) -> str:
    secs = int(secs)
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m {secs % 60}s"
    return f"{secs // 3600}h {(secs % 3600) // 60}m"


# ─────────────────────────────────────────────────────────────────────────────
# Not saying the same thing twelve times an hour
# ─────────────────────────────────────────────────────────────────────────────

def should_alert(result: dict, now_utc: datetime, state: dict) -> tuple[bool, str]:
    """Decide whether this result is worth interrupting Chandan for.

    Returns (send, kind) where kind is 'new', 'repeat' or 'recovered'.

    The recovery message is not a nicety. Once an alarm has fired, silence has
    two meanings — fixed, or the watchdog died too — and only one of them is
    good news. Saying so explicitly removes the ambiguity.
    """
    was_alarming = state.get("alarming", False)

    # No news. Not good news. See _result's docstring for the false all-clear
    # this prevents at 16:00 every day.
    if not result.get("informative", True):
        return (False, "no news")

    if result["severity"] == "ok":
        return (was_alarming, "recovered")

    if not was_alarming:
        return (True, "new")

    last = state.get("last_alert_utc")
    if not last:
        return (True, "new")
    elapsed = (now_utc - datetime.fromisoformat(last)).total_seconds() / 60
    return (elapsed >= config.WATCHDOG_REALERT_MINUTES, "repeat")


def load_state() -> dict:
    """Never raises. A corrupt note must not stop the check that matters —
    the worst case of ignoring it is one duplicate alert."""
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict) -> None:
    try:
        STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as exc:
        print(f"  (could not save watchdog state: {type(exc).__name__}: {exc})")


# ─────────────────────────────────────────────────────────────────────────────
# Telling someone
# ─────────────────────────────────────────────────────────────────────────────

def notify_desktop(title: str, message: str) -> bool:
    """A Windows notification, using only what ships with the OS.

    Deliberately not a library: an alerting path with a dependency is a path
    that breaks on the day the environment is rebuilt, which is a plausible day
    for the collector to be down too.
    """
    ps = (
        '[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications,'
        ' ContentType=WindowsRuntime] > $null;'
        '$t=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(1);'
        f'$t.GetElementsByTagName("text").Item(0).AppendChild($t.CreateTextNode({_ps_str(title)})) > $null;'
        f'$t.GetElementsByTagName("text").Item(1).AppendChild($t.CreateTextNode({_ps_str(message)})) > $null;'
        '[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier'
        '("SPX Collector Watchdog").Show([Windows.UI.Notifications.ToastNotification]::new($t));'
    )
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       check=True, capture_output=True, timeout=30)
        return True
    except Exception as exc:
        print(f"  desktop notification failed ({type(exc).__name__}); "
              f"falling back to a message box")
        return _message_box(title, message)


def _message_box(title: str, message: str) -> bool:
    """Fallback: a plain dialog. Uglier, and far harder to miss."""
    try:
        # 0x40 information, 0x1000 top-most so it does not open behind the
        # window you are working in — which for an alarm defeats the purpose.
        ctypes.windll.user32.MessageBoxW(0, message, title, 0x40 | 0x1000)
        return True
    except Exception as exc:
        print(f"  message box failed too ({type(exc).__name__})")
        return False


def _ps_str(s: str) -> str:
    """A PowerShell single-quoted literal. Quotes doubled, newlines flattened."""
    return "'" + s.replace("'", "''").replace("\r", " ").replace("\n", " ") + "'"


def notify_email(subject: str, body: str) -> bool:
    """Email, if .env has been filled in. Skipped quietly if not.

    Not configuring email is a legitimate choice, so an empty mailbox is not
    an error — but a configured mailbox that FAILS is, and says so loudly,
    because a silently broken alert channel is worse than no channel: you
    believe you are covered.
    """
    if not config.ALERT_EMAIL_TO:
        print("  email: not configured (ALERT_EMAIL_TO empty in .env) — skipped")
        return False
    if not config.ALERT_SMTP_PASSWORD:
        print("  email: ALERT_EMAIL_TO is set but ALERT_SMTP_PASSWORD is empty — "
              "NOT SENT. Half-configured is the dangerous state; finish .env.")
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.ALERT_EMAIL_FROM or config.ALERT_EMAIL_TO
    msg["To"] = config.ALERT_EMAIL_TO
    msg.set_content(body)

    try:
        with smtplib.SMTP(config.ALERT_SMTP_HOST, config.ALERT_SMTP_PORT, timeout=30) as s:
            s.starttls()
            s.login(config.ALERT_EMAIL_FROM or config.ALERT_EMAIL_TO,
                    config.ALERT_SMTP_PASSWORD)
            s.send_message(msg)
        print(f"  email: sent to {config.ALERT_EMAIL_TO}")
        return True
    except Exception as exc:
        # The password is never echoed, here or anywhere.
        print(f"  email: FAILED — {type(exc).__name__}: {exc}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

_ICON = {"ok": "✅", "warn": "⚠️", "alarm": "🚨"}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Check that prices are still arriving.")
    p.add_argument("--dry-run", action="store_true",
                   help="report what it found and send nothing")
    p.add_argument("--test-alert", action="store_true",
                   help="send a pop-up and an email now, to prove they work")
    args = p.parse_args(argv)

    if args.test_alert:
        print("Sending a TEST alert on both channels...")
        d = notify_desktop("SPX watchdog — test",
                           "This is a test. Collection is not affected.")
        e = notify_email("SPX watchdog — test alert",
                         "This is a test of the collector watchdog's email path.\n"
                         "If you are reading this, alerts will reach you.\n"
                         "Nothing is wrong.")
        print(f"\n  desktop: {'ok' if d else 'FAILED'}   email: {'ok' if e else 'not sent'}")
        return 0 if d else 2

    now = datetime.now(UTC)
    try:
        result = check(now)
    except Exception as exc:
        print(f"🚨 The watchdog itself failed: {type(exc).__name__}: {exc}")
        return 2

    print(f"{_ICON[result['severity']]} {result['headline']}")
    for line in result["detail"].splitlines():
        print(f"   {line}")

    state = load_state()
    send, kind = should_alert(result, now, state)

    if args.dry_run:
        print(f"\n  --dry-run: would {'SEND a ' + kind + ' alert' if send else 'send nothing'}.")
        return 0 if result["ok"] else 1

    if send:
        if kind == "recovered":
            title = "SPX collector: recovered"
            body = ("Prices are arriving again.\n\n"
                    f"{result['headline']}\n{result['detail']}")
        else:
            title = f"SPX collector: {result['headline']}"
            body = (f"{result['headline']}\n\n{result['detail']}\n\n"
                    f"Checked at {now:%Y-%m-%d %H:%M:%S} UTC.")
        print(f"\n  alerting ({kind}):")
        notify_desktop(title, body)
        notify_email(title, body)

    # An uninformative check must not overwrite what we last actually KNEW.
    # Clearing the flag overnight would lose the fact that collection was
    # broken when the bell rang, and the morning's first real check would
    # report it as brand new — which is survivable, but the note is supposed
    # to be a record of the last thing observed, not of the last thing asked.
    alarming = state.get("alarming", False) if not result.get("informative", True) \
        else not result["ok"]

    save_state({
        "alarming": alarming,
        "last_alert_utc": now.isoformat() if send else state.get("last_alert_utc"),
        "last_check_utc": now.isoformat(),
        "last_headline": result["headline"],
    })

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
