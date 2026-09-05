"""M4.5 — a shared token, because there is exactly one user.

WHY NOT ACCOUNTS. ADR-012 settled the access model: one machine, one user,
reachable from a phone over LAN or Tailscale. Multi-user and SaaS are on the
"Do Not Build" list in DOCUMENTATION.md §8.4. User accounts would mean a user
table, password hashing, session management and a password-reset path — all of
it guarding a single person's read-only view of their own data, and all of it
new code that can be wrong. A shared secret is the honest size of the problem.

WHAT THIS DOES AND DOES NOT PROTECT AGAINST. It stops a device on the same
network — a guest phone, a smart TV, anything that joined the Wi-Fi — from
reading the record by guessing a port. It does NOT make the API safe to expose
to the internet, and no amount of token length would: there is no rate
limiting, no audit log, and no revocation beyond changing the value and
restarting. The exposure model is the tunnel, not the token; the token is what
stops the tunnel being the only thing standing between the record and the
network.

OFF BY DEFAULT, AND THAT IS DELIBERATE. With no token configured the API is
open and bound to localhost, which is the correct shape for the machine it
runs on — the dashboard beside it has never asked for a password either.
Setting SPX_API_TOKEN turns enforcement on. The alternative (fail to start
without a token) would mean a local development server that cannot run until a
secret is invented, and the predictable result is a token of "x" committed to
a shell script.
"""
from __future__ import annotations

import hmac
import logging
import os

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

TOKEN_ENV_VAR = "SPX_API_TOKEN"
HEADER = "X-API-Token"

# Paths reachable without the token. /health is here so a monitor can confirm
# the server is up without holding the secret; it publishes no market data —
# a snapshot id, a timestamp and a spot price, all of which a caller must
# already be inside the tunnel to reach.
_OPEN_PATHS = frozenset({"/health"})


def configured_token() -> str | None:
    """The token, or None when enforcement is off.

    An empty or whitespace-only value counts as absent. `SPX_API_TOKEN=` in a
    .env file is a line someone wrote while turning the feature OFF, and
    treating the empty string as a valid secret would make every request fail
    with a message about a token they thought they had removed.
    """
    raw = os.environ.get(TOKEN_ENV_VAR, "")
    return raw.strip() or None


def check(request: Request) -> None:
    """Raise 401 unless the request carries the configured token.

    COMPARED WITH `hmac.compare_digest`, not `==`. String equality returns as
    soon as it finds a differing byte, so the time it takes leaks how much of
    the token was right — enough, over many attempts, to recover it one
    character at a time. The comparison here takes the same time whatever the
    input.
    """
    expected = configured_token()
    if expected is None:
        return
    if request.url.path in _OPEN_PATHS:
        return

    supplied = request.headers.get(HEADER, "")
    if not hmac.compare_digest(supplied, expected):
        # The reason is not reported back. "No token" and "wrong token" are
        # different facts and telling them apart helps whoever is guessing far
        # more than it helps whoever forgot to set a header.
        raise HTTPException(
            status_code=401,
            detail=f"This API requires the {HEADER} header.",
        )


def describe() -> str:
    """One line for the startup log, without printing the secret."""
    return ("token required" if configured_token()
            else "OPEN — no token set, localhost only")
