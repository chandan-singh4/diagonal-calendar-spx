"""
reauth.py — Re-authenticate with Schwab. Run this every ~7 days.

WHY THIS SCRIPT EXISTS
----------------------
The obvious command does not work:

    python -c "import schwab_client; schwab_client.get_client()"

`get_client()` only starts the login flow when the token file is ABSENT. If
`data/token.json` still exists — which it does, right up until it expires — that
call quietly loads the stale token and returns. Nothing happens, no error is
printed, and you find out on Monday morning when the collector stops.

The dashboard's own expiry banner recommended that exact command until
2026-07-26, so this trap was actively being taught.

This script does the whole thing correctly:

  1. moves the existing token aside (does not delete it),
  2. runs the manual OAuth flow,
  3. on success, reports when the new token expires and removes the old copy,
  4. **on failure or abort, puts the old token back.**

Step 4 is the point. Aborting halfway through the browser flow used to leave you
with no token at all — strictly worse than the nearly-expired one you started
with, and with the collector down until you retried.

THE SPLIT FLOW, AND WHY IT WAS ADDED (2026-09-13)
-------------------------------------------------
The flow above assumes a keyboard: its last step is `input()` waiting for a
pasted redirect URL. That makes the terminal the ONLY route, which is a real
availability problem — the token dies seven days after a login, on whatever day
that lands, and "I was away from that machine on day seven" is exactly how the
2026-08-09 lapse happened. Prices missed during a lapse are gone for good.

So the interactive flow is split into two halves that can be run minutes apart,
from different places:

    python scripts/reauth.py --begin           # prints the login URL
    python scripts/reauth.py --finish "<url>"  # completes with the address back

Nothing is destroyed by `--begin`: it does not touch the token, so an abandoned
half-flow costs nothing and a still-valid token keeps the collector running.
That is the opposite of the interactive path, which must delete the token up
front (see `_interactive`), and it is why `--begin` is safe to run speculatively.

The two halves are tied together by the `state` parameter — a random string
issued with the URL and echoed back by Schwab. `_finish` checks that it matches,
so a redirect URL from anywhere else is refused. The context is single-use: a
successful `--finish` deletes it.

USAGE
-----
    python scripts/reauth.py                   # re-authenticate, interactively
    python scripts/reauth.py --begin           # start a split re-auth
    python scripts/reauth.py --finish "<url>"  # finish one
    python scripts/reauth.py --check           # report time remaining, change nothing

Schwab still requires a real browser and a real person. The split does not
automate that away and is not meant to — it only removes the requirement that
the person be sitting at this particular terminal.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import schwab.auth

import config
import schwab_client

# Schwab expires refresh tokens 7 days after the interactive login. Not
# configurable by us — it is their policy.
REFRESH_TOKEN_LIFETIME_DAYS = 7.0

# Where the half-finished login is parked between --begin and --finish. Beside
# the token rather than in a temp directory, so the two halves agree on the
# location without an extra setting, and so a stray context is found by anyone
# looking at data/ rather than hiding in %TEMP%.
CONTEXT_FILENAME = ".reauth_context.json"

# A login left open longer than this probably belongs to a forgotten attempt.
# It is a WARNING, not a refusal: Schwab decides whether the code is still good,
# and refusing here would block a legitimate slow login (two-factor by text, a
# password reset) for no reason beyond our own guess about how long it takes.
CONTEXT_STALE_MINUTES = 30


def _context_path() -> Path:
    return Path(config.SCHWAB_TOKEN_PATH).parent / CONTEXT_FILENAME


def _report_age() -> float | None:
    age = schwab_client.get_token_age_days()
    if age is None:
        print("No token found - this will be a first-time login.")
        return None

    remaining = REFRESH_TOKEN_LIFETIME_DAYS - age
    expires_at = datetime.now() + timedelta(days=remaining)
    print(f"Current token: {age:.1f} days old, {remaining:.1f} days remaining.")
    if remaining <= 0:
        print("  STATUS: EXPIRED - the collector cannot fetch data until you re-authenticate.")
    elif remaining < 1:
        print(f"  STATUS: expires {expires_at:%A %d %B at %H:%M} - re-authenticate today.")
    else:
        print(f"  STATUS: OK - expires {expires_at:%A %d %B at %H:%M}.")
    return remaining


def _backup_token() -> tuple[Path, Path]:
    """Copy the token aside. Returns (token_path, backup_path).

    A COPY, not a move, and taken before anything else can fail. The backup is
    removed only once a new token has been written AND verified readable.
    """
    token_path = Path(config.SCHWAB_TOKEN_PATH)
    backup = token_path.with_suffix(f".json.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    if token_path.exists():
        shutil.copy2(token_path, backup)
    return token_path, backup


def _restore(token_path: Path, backup: Path, why: str, remaining: float | None) -> int:
    if backup.exists():
        shutil.copy2(backup, token_path)
        print(f"\nLogin did not complete ({why}). Your previous token has been restored.")
        if remaining is not None and remaining > 0:
            print(f"It still has {remaining:.1f} days left - the collector keeps running.")
    else:
        print(f"\nLogin did not complete ({why}), and there was no previous token to restore.")
    return 1


def _token_write_func(token_path: Path):
    """The writer handed to schwab-py. Writes the token AND NOTHING ELSE.

    DO NOT add `creation_timestamp` here. `client_from_received_url` wraps this
    function in `TokenMetadata.wrapped_token_write_func`, which adds the
    metadata itself. Adding it here too produces a doubly-nested file —
    {"creation_timestamp": .., "token": {"creation_timestamp": .., "token": ..}}
    — which `client_from_token_file` loads without complaint and then fails on,
    because the inner "token" is a metadata envelope and not a token. The
    collector would go blind holding a token file that looks perfectly fine.
    """
    def update_token(token, *args, **kwargs):
        with open(token_path, "w") as handle:
            json.dump(token, handle)
    return update_token


def _verify_token_file(token_path: Path) -> str | None:
    """Returns None if the written token is loadable, else why it is not.

    CHECKED RATHER THAN ASSUMED because the failure this guards against is
    silent: every way of getting it wrong produces a file of plausible size at
    the right path, and the only symptom is the collector recording nothing.
    Better to fail here, while the old token is still on disk, than at 09:30.
    """
    if not token_path.exists():
        return "no token file was written"
    try:
        data = json.loads(token_path.read_text())
    except (OSError, ValueError) as exc:
        return f"the token file is not readable JSON ({exc})"
    inner = data.get("token")
    nested = isinstance(inner, dict)
    for wrong, problem in (
        ("creation_timestamp" not in data,
         "the token file has no creation_timestamp - the 7-day clock could not be read"),
        (not nested,
         "the token file has no token object inside its metadata"),
        (nested and ("token" in inner or "creation_timestamp" in inner),
         "the token was written twice-wrapped - see _token_write_func"),
        (nested and "refresh_token" not in inner,
         "the token carries no refresh_token - the collector could not stay signed in"),
    ):
        if wrong:
            return problem
    return None


def _looks_expired(exc: BaseException) -> bool:
    """Is this failure a used-up authorization code rather than a real fault?

    MATCHED ON THE TEXT because schwab-py raises authlib's OAuthError, whose
    type says only that OAuth failed. The distinction matters to whoever is
    reading: "invalid_grant" means try again faster, while a state mismatch or
    a bad app secret means stop and look at something. Same error class, two
    completely different next actions, so the message is worth parsing.
    """
    text = str(exc).lower()
    return "invalid_grant" in text or "expired or revoked" in text


def _announce_expiry(token_path: Path) -> None:
    try:
        created = json.loads(token_path.read_text()).get("creation_timestamp")
    except (OSError, ValueError, TypeError):
        return
    if not created:
        return
    expiry = datetime.fromtimestamp(float(created)) + timedelta(
        days=REFRESH_TOKEN_LIFETIME_DAYS)
    print(f"New token valid until {expiry:%A %d %B at %H:%M}. "
          f"Set a reminder for {expiry - timedelta(days=1):%A %d %B}.")


def _report_success(token_path: Path, backup: Path) -> int:
    print("\nRe-authenticated successfully.")
    _announce_expiry(token_path)
    if backup.exists():
        backup.unlink()
        print("Old token removed.")
    print("\nThe collector picks up the new token on its next cycle - no restart needed.")
    return 0


def _begin() -> int:
    """Print the Schwab login URL and park the context. Touches no token."""
    config.validate()
    context = schwab.auth.get_auth_context(
        config.SCHWAB_APP_KEY, config.SCHWAB_CALLBACK_URL)

    path = _context_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "callback_url": context.callback_url,
        "authorization_url": context.authorization_url,
        "state": context.state,
        "created_at": int(time.time()),
    }), encoding="utf-8")

    print("\n1. Open this address in a browser and log in, then click Allow:\n")
    print(f"   {context.authorization_url}\n")
    print("2. The page will then look like an error. That is expected and correct -")
    print("   it is redirecting to your own machine, where nothing is listening.")
    print("   The address bar is the part that matters.\n")
    print("3. Copy the ENTIRE address from the address bar and run:\n")
    print('   python scripts/reauth.py --finish "<paste the address here>"\n')
    print("Your existing token has NOT been touched. If you abandon this login,")
    print("nothing has changed and the collector carries on.")
    return 0


def _finish(received_url: str) -> int:
    """Exchange a pasted redirect URL for a new token."""
    config.validate()
    path = _context_path()
    if not path.exists():
        print(f"No login in progress - {path.name} is not there.")
        print("Run `python scripts/reauth.py --begin` first, and use the URL it prints.")
        return 1

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        context = schwab.auth.AuthContext(
            raw["callback_url"], raw["authorization_url"], raw["state"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"The parked login is unreadable ({exc}). Run --begin again.")
        return 1

    age_minutes = (time.time() - float(raw.get("created_at", 0))) / 60
    if age_minutes > CONTEXT_STALE_MINUTES:
        print(f"NOTE: this login was started {age_minutes:.0f} minutes ago. Schwab may have")
        print("      expired it. If the exchange below fails, run --begin for a fresh URL.\n")

    age = schwab_client.get_token_age_days()
    remaining = None if age is None else REFRESH_TOKEN_LIFETIME_DAYS - age

    # STRIPPED of quotes because the address is pasted through a shell, and both
    # PowerShell and bash hand it over still wearing them often enough that
    # failing on it would be a poor first experience of the new flag.
    received_url = received_url.strip().strip('"').strip("'")

    token_path, backup = _backup_token()
    if backup.exists():
        print(f"Existing token copied aside -> {backup.name}")

    try:
        schwab.auth.client_from_received_url(
            config.SCHWAB_APP_KEY, config.SCHWAB_APP_SECRET, context,
            received_url, _token_write_func(token_path))
    except BaseException as exc:
        # The context is deliberately LEFT IN PLACE here: one likely cause is a
        # mistyped or truncated paste, and making someone redo the browser login
        # to fix their own copy-paste would be unkind.
        outcome = _restore(token_path, backup, f"{type(exc).__name__}: {exc}", remaining)
        if _looks_expired(exc):
            print("\nThat means the CODE ran out, not that anything is misconfigured.")
            print("Schwab's codes live for a couple of minutes, so the address has to be")
            print("pasted back soon after you click Allow - a login left open while you")
            print("do something else will always fail here.")
            print("\nRun `--begin` for a fresh address and finish that one promptly.")
        return outcome

    problem = _verify_token_file(token_path)
    if problem is not None:
        return _restore(token_path, backup, problem, remaining)

    # SINGLE USE. A context left lying around is a login that could be completed
    # twice, and a stale one is the confusing failure this flag most invites.
    path.unlink(missing_ok=True)
    return _report_success(token_path, backup)


def _interactive(remaining: float | None) -> int:
    """The original flow: browser opens, redirect URL pasted at a prompt.

    THE TOKEN IS DELETED UP FRONT here, unlike --begin, and that is forced:
    `get_client()` only starts a login when the token file is absent, so there
    is no way to reach the login without removing it first. That is precisely
    the risk the split flow avoids, and the reason --begin is preferable when
    you have the choice.
    """
    token_path, backup = _backup_token()
    if token_path.exists():
        token_path.unlink()
        print(f"\nExisting token moved aside -> {backup.name}")
        print("It will be restored automatically if this login does not complete.\n")

    print("A browser will open for the Schwab login.")
    print("After you click Allow, copy the FULL redirected URL from the address bar")
    print("and paste it back here. The page will look like an error - that is expected.\n")

    try:
        schwab_client.get_client()
    except BaseException as exc:  # includes KeyboardInterrupt / SystemExit
        return _restore(token_path, backup, type(exc).__name__, remaining)

    problem = _verify_token_file(token_path)
    if problem is not None:
        return _restore(token_path, backup, problem, remaining)

    return _report_success(token_path, backup)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Re-authenticate with Schwab.")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true",
                      help="Report time remaining and exit without changing anything.")
    mode.add_argument("--begin", action="store_true",
                      help="Print the Schwab login URL and park it. Touches no token.")
    mode.add_argument("--finish", metavar="URL",
                      help="Complete a --begin login with the address Schwab redirected to.")
    args = ap.parse_args(argv)

    if args.begin:
        return _begin()
    if args.finish:
        return _finish(args.finish)

    remaining = _report_age()
    if args.check:
        return 0
    return _interactive(remaining)


if __name__ == "__main__":
    raise SystemExit(main())
