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

USAGE
-----
    python scripts/reauth.py            # re-authenticate
    python scripts/reauth.py --check    # just report time remaining, change nothing

This is INTERACTIVE by design: Schwab requires a browser login and a copy-pasted
redirect URL. It cannot be scheduled or automated, and that is a deliberate
security boundary, not a gap to engineer around.
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

import config  # noqa: E402
import schwab_client  # noqa: E402

# Schwab expires refresh tokens 7 days after the interactive login. Not
# configurable by us — it is their policy.
REFRESH_TOKEN_LIFETIME_DAYS = 7.0


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


def main() -> int:
    ap = argparse.ArgumentParser(description="Re-authenticate with Schwab.")
    ap.add_argument("--check", action="store_true",
                    help="Report time remaining and exit without changing anything.")
    args = ap.parse_args()

    remaining = _report_age()
    if args.check:
        return 0

    token_path = Path(config.SCHWAB_TOKEN_PATH)
    backup = token_path.with_suffix(f".json.bak-{time.strftime('%Y%m%d-%H%M%S')}")

    if token_path.exists():
        shutil.copy2(token_path, backup)
        token_path.unlink()
        print(f"\nExisting token moved aside -> {backup.name}")
        print("It will be restored automatically if this login does not complete.\n")

    print("A browser will open for the Schwab login.")
    print("After you click Allow, copy the FULL redirected URL from the address bar")
    print("and paste it back here. The page will look like an error - that is expected.\n")

    try:
        schwab_client.get_client()
    except BaseException as exc:  # includes KeyboardInterrupt / SystemExit
        # Put the old token back. It may be nearly expired, but "nearly expired"
        # beats "gone" -- the collector keeps working until it lapses.
        if backup.exists():
            shutil.copy2(backup, token_path)
            print(f"\nLogin did not complete ({type(exc).__name__}). "
                  f"Your previous token has been restored.")
            if remaining is not None and remaining > 0:
                print(f"It still has {remaining:.1f} days left - the collector keeps running.")
        else:
            print(f"\nLogin did not complete ({type(exc).__name__}), and there was no "
                  f"previous token to restore.")
        return 1

    if not token_path.exists():
        print("\nWARNING: the login reported success but no token file was written.")
        if backup.exists():
            shutil.copy2(backup, token_path)
            print("Previous token restored.")
        return 1

    print("\nRe-authenticated successfully.")
    try:
        created = json.loads(token_path.read_text()).get("creation_timestamp")
        if created:
            expiry = datetime.fromtimestamp(float(created)) + timedelta(
                days=REFRESH_TOKEN_LIFETIME_DAYS)
            print(f"New token valid until {expiry:%A %d %B at %H:%M}. "
                  f"Set a reminder for {expiry - timedelta(days=1):%A %d %B}.")
    except (OSError, ValueError, TypeError):
        pass

    if backup.exists():
        backup.unlink()
        print("Old token removed.")

    print("\nThe collector picks up the new token on its next cycle - no restart needed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())