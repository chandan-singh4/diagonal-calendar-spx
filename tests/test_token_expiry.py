"""
Tests for the Schwab token-expiry warning (M1.6, partial).

Schwab expires the refresh token 7 days after an interactive login. Renewing it
needs a browser login and a copy-pasted redirect URL, so it cannot be automated
— that is a deliberate security boundary, not a gap to engineer around.

What CAN be made reliable is the warning. Until 2026-07-26 the collector only
noticed after the fact: the token lapsed, calls failed, and a session's prices
were lost before anyone read the log. These tests cover the arithmetic that
decides when to warn.

The threshold constants are asserted explicitly rather than imported into the
expectations, so that widening the warning window is a deliberate change with a
visible diff — not something that silently shortens the notice period.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

COLLECTOR_PATH = Path(__file__).resolve().parent.parent / "collector.py"


def _load_token_helpers() -> dict:
    """Pull the pure token helpers out of collector.py.

    collector.py's module level is import-safe, but importing it pulls in
    schwab-py and the whole config/db stack for two lines of arithmetic. The AST
    extraction keeps this test fast and dependency-free, consistent with the
    other loaders in this directory.
    """
    tree = ast.parse(COLLECTOR_PATH.read_text(encoding="utf-8"))
    wanted_f = {"token_days_remaining"}
    wanted_c = {"_REFRESH_TOKEN_LIFETIME_DAYS", "_TOKEN_WARN_AFTER_DAYS",
                "_TOKEN_CHECK_INTERVAL_SEC"}

    picked = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if names & wanted_c:
                picked.append(node)
                wanted_c -= names
        elif isinstance(node, ast.FunctionDef) and node.name in wanted_f:
            picked.append(node)
            wanted_f.discard(node.name)

    missing = sorted(wanted_f | wanted_c)
    assert not missing, f"collector.py no longer defines {missing}"

    picked.sort(key=lambda n: 0 if isinstance(n, ast.Assign) else 1)
    ns: dict = {"__builtins__": __builtins__}
    mod = ast.Module(body=picked, type_ignores=[])
    ast.fix_missing_locations(mod)
    exec(compile(mod, filename=str(COLLECTOR_PATH), mode="exec"), ns)  # noqa: S102
    return ns


@pytest.fixture(scope="module")
def tok():
    return _load_token_helpers()


def test_schwab_lifetime_is_seven_days(tok):
    """Schwab's policy, not our choice. If this changes, Schwab changed it."""
    assert tok["_REFRESH_TOKEN_LIFETIME_DAYS"] == 7.0


def test_warning_gives_at_least_a_full_day_of_notice(tok):
    """The warning must fire with time left to act on it.

    A threshold at or above the 7-day lifetime would warn only once the token had
    already expired, which is precisely the silent failure this replaced.
    """
    warn_at = tok["_TOKEN_WARN_AFTER_DAYS"]
    lifetime = tok["_REFRESH_TOKEN_LIFETIME_DAYS"]
    assert warn_at < lifetime
    assert lifetime - warn_at >= 1.0, "less than a day's notice is not enough"


@pytest.mark.parametrize(
    "age, expected_remaining",
    [
        (0.0, 7.0),
        (1.0, 6.0),
        (6.0, 1.0),
        (6.1, 0.9),      # the real state on 2026-07-26
        (7.0, 0.0),      # exactly expired
        (9.5, -2.5),     # expired days ago — must go negative, not clamp to 0
    ],
)
def test_days_remaining(tok, age, expected_remaining):
    assert tok["token_days_remaining"](age) == pytest.approx(expected_remaining)


def test_days_remaining_is_none_when_age_is_unknown(tok):
    """A missing or unreadable token file yields None, not a misleading 7.0."""
    assert tok["token_days_remaining"](None) is None


def test_expiry_is_detectable_as_a_sign_change(tok):
    """Callers distinguish expired from valid by the sign, so it must not clamp."""
    assert tok["token_days_remaining"](6.99) > 0
    assert tok["token_days_remaining"](7.01) < 0


def test_hourly_recheck_interval_is_sane(tok):
    """Frequent enough to keep warning, rare enough to be free.

    The value only changes on a re-login, so anything under a minute would be
    pure noise in the log, and anything over a day could miss the window.
    """
    assert 60 <= tok["_TOKEN_CHECK_INTERVAL_SEC"] <= 86400
