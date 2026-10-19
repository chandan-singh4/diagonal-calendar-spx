"""
reauth.py — the split login flow, and the shape of the file it writes.

WHAT THESE TESTS ARE FOR. Everything this script does is invisible until the
next market open. A token written in the wrong shape is a file of plausible
size at the right path; a `--begin` that quietly clobbered the live token would
not be noticed until the collector stopped. Both failures cost irreplaceable
data, and neither announces itself, so they are asserted here instead.

The regression that prompted the file is `test_the_writer_does_not_add_metadata`.
`client_from_received_url` wraps the caller's write function in
`TokenMetadata.wrapped_token_write_func`, which adds `creation_timestamp`
itself. A first draft of the split flow added it in the writer TOO, producing a
doubly-nested file that loads as JSON, passes an existence check, and fails only
when the collector tries to use it.

Every test drives the script against a temporary token path — config is
monkeypatched — and the first test asserts that the patch takes, because a leak
would mean these tests destroying the real Schwab login while reporting success.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
import schwab.auth

import config
import schwab_client

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import reauth  # noqa: E402  — requires the sys.path line above

# Captured before any fixture patches config, so the guard test below has a
# truthful "this is the production token" to compare against.
REAL_TOKEN_PATH = config.SCHWAB_TOKEN_PATH

FAKE_TOKEN = {
    "access_token": "access-xxx",
    "refresh_token": "refresh-xxx",
    "token_type": "Bearer",
    "expires_in": 1800,
    "expires_at": 9999999999,
}


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """A temporary token path, with app credentials that never leave the test."""
    token_path = tmp_path / "token.json"
    monkeypatch.setattr(config, "SCHWAB_TOKEN_PATH", str(token_path))
    monkeypatch.setattr(config, "SCHWAB_APP_KEY", "test-app-key")
    monkeypatch.setattr(config, "SCHWAB_APP_SECRET", "test-app-secret")
    monkeypatch.setattr(config, "SCHWAB_CALLBACK_URL", "https://127.0.0.1")
    monkeypatch.setattr(config, "validate", lambda: None)
    return token_path


def _write_live_token(token_path: Path, created: int | None = None) -> None:
    token_path.write_text(json.dumps({
        "creation_timestamp": created or int(time.time()),
        "token": FAKE_TOKEN,
    }))


def test_the_sandbox_is_not_the_real_token(sandbox):
    """If this fails, every test below is running against the live login."""
    assert str(sandbox) != REAL_TOKEN_PATH
    assert str(sandbox) == config.SCHWAB_TOKEN_PATH


# ─────────────────────────────────────────────────────────────────────────────
# The shape of the file on disk
# ─────────────────────────────────────────────────────────────────────────────

def test_the_writer_does_not_add_metadata(sandbox):
    """THE REGRESSION. The writer must record the token and nothing else.

    schwab-py adds `creation_timestamp` in its own wrapper. A writer that adds
    it too nests the file twice, and `client_from_token_file` then reads a
    metadata envelope where it expects a token.
    """
    reauth._token_write_func(sandbox)(FAKE_TOKEN)

    written = json.loads(sandbox.read_text())
    assert written == FAKE_TOKEN
    assert "creation_timestamp" not in written


def test_the_writer_under_the_real_wrapper_produces_a_readable_token(sandbox):
    """The round trip, through the LIBRARY'S OWN wrapper rather than a mock.

    This is the assertion that would have caught the bug: it composes our
    writer with the real `TokenMetadata` and then reads the result back with
    the same function the dashboard's expiry banner uses.
    """
    created = int(time.time())
    metadata = schwab.auth.TokenMetadata(
        FAKE_TOKEN, created, reauth._token_write_func(sandbox))
    metadata.wrapped_token_write_func()(FAKE_TOKEN)

    on_disk = json.loads(sandbox.read_text())
    assert set(on_disk) == {"creation_timestamp", "token"}
    assert on_disk["token"] == FAKE_TOKEN
    assert reauth._verify_token_file(sandbox) is None

    age = schwab_client.get_token_age_days()
    assert age is not None and age < 0.01


def test_verify_rejects_a_twice_wrapped_token(sandbox):
    sandbox.write_text(json.dumps({
        "creation_timestamp": int(time.time()),
        "token": {"creation_timestamp": int(time.time()), "token": FAKE_TOKEN},
    }))
    assert "twice-wrapped" in reauth._verify_token_file(sandbox)


def test_verify_rejects_a_token_with_no_refresh_token(sandbox):
    """A token without a refresh token works for thirty minutes and then stops."""
    sandbox.write_text(json.dumps({
        "creation_timestamp": int(time.time()),
        "token": {k: v for k, v in FAKE_TOKEN.items() if k != "refresh_token"},
    }))
    assert "refresh_token" in reauth._verify_token_file(sandbox)


def test_verify_rejects_a_missing_file(sandbox):
    assert reauth._verify_token_file(sandbox) == "no token file was written"


# ─────────────────────────────────────────────────────────────────────────────
# --begin
# ─────────────────────────────────────────────────────────────────────────────

def test_begin_does_not_touch_an_existing_token(sandbox, capsys):
    """THE WHOLE POINT OF THE SPLIT. An abandoned --begin must cost nothing."""
    _write_live_token(sandbox)
    before = sandbox.read_bytes()

    assert reauth.main(["--begin"]) == 0

    assert sandbox.read_bytes() == before
    assert not list(sandbox.parent.glob("*.bak-*")), "--begin took a backup it did not need"


def test_begin_prints_a_schwab_login_url_and_parks_the_state(sandbox, capsys):
    assert reauth.main(["--begin"]) == 0
    printed = capsys.readouterr().out

    parked = json.loads(reauth._context_path().read_text())
    assert parked["state"] in printed
    assert parked["authorization_url"] in printed
    assert parked["authorization_url"].startswith(
        "https://api.schwabapi.com/v1/oauth/authorize")
    assert "test-app-key" in parked["authorization_url"]


def test_begin_issues_a_new_state_each_time(sandbox):
    reauth.main(["--begin"])
    first = json.loads(reauth._context_path().read_text())["state"]
    reauth.main(["--begin"])
    second = json.loads(reauth._context_path().read_text())["state"]
    assert first != second, "a reused state would defeat the check in --finish"


# ─────────────────────────────────────────────────────────────────────────────
# --finish
# ─────────────────────────────────────────────────────────────────────────────

def test_finish_without_a_begin_refuses_and_leaves_the_token_alone(sandbox, capsys):
    _write_live_token(sandbox)
    before = sandbox.read_bytes()

    assert reauth.main(["--finish", "https://127.0.0.1/?code=abc"]) == 1

    assert sandbox.read_bytes() == before
    assert "No login in progress" in capsys.readouterr().out


def test_finish_restores_the_old_token_when_the_exchange_fails(
        sandbox, monkeypatch, capsys):
    """A bad paste must not cost the token that is still working."""
    _write_live_token(sandbox)
    before = sandbox.read_bytes()
    reauth.main(["--begin"])

    def explode(*args, **kwargs):
        raise ValueError("mismatching_state")
    monkeypatch.setattr("schwab.auth.client_from_received_url", explode)

    assert reauth.main(["--finish", "https://127.0.0.1/?code=wrong"]) == 1

    assert sandbox.read_bytes() == before, "the previous token was not restored"
    out = capsys.readouterr().out
    assert "mismatching_state" in out
    assert "restored" in out
    # The context SURVIVES a failure, so a mistyped paste can be retried
    # without going back through the browser.
    assert reauth._context_path().exists()


def test_finish_writes_the_token_and_consumes_the_context(
        sandbox, monkeypatch, capsys):
    _write_live_token(sandbox, created=int(time.time()) - 600_000)
    reauth.main(["--begin"])

    captured = {}

    def exchange(api_key, app_secret, context, received_url, token_write_func):
        # Mimics the real function: it is the LIBRARY that adds the metadata.
        captured["state"] = context.state
        captured["url"] = received_url
        metadata = schwab.auth.TokenMetadata(
            FAKE_TOKEN, int(time.time()), token_write_func)
        metadata.wrapped_token_write_func()(FAKE_TOKEN)

    monkeypatch.setattr("schwab.auth.client_from_received_url", exchange)

    assert reauth.main(["--finish", '  "https://127.0.0.1/?code=abc&state=xyz"  ']) == 0

    # Quotes and whitespace from a shell paste are stripped before use.
    assert captured["url"] == "https://127.0.0.1/?code=abc&state=xyz"
    assert reauth._verify_token_file(sandbox) is None
    assert json.loads(sandbox.read_text())["token"] == FAKE_TOKEN

    assert not reauth._context_path().exists(), "the context must be single-use"
    assert not list(sandbox.parent.glob("*.bak-*")), "the backup was not cleaned up"
    assert "Re-authenticated successfully" in capsys.readouterr().out


def test_finish_explains_an_expired_authorization_code(sandbox, monkeypatch, capsys):
    """The failure this flow invites most, and the one to name explicitly.

    Schwab's authorization codes live for a couple of minutes, so a login left
    open while you do something else always fails here. The raw message —
    "unsupported_token_type ... invalid_grant" — reads like a misconfiguration
    and sends you looking at `.env`, which is exactly the wrong place. The text
    below is the real one, copied from a live failure on 2026-09-13.
    """
    _write_live_token(sandbox)
    reauth.main(["--begin"])

    def stale(*args, **kwargs):
        raise ValueError(
            'unsupported_token_type: 400 Bad Request: {"error_description":'
            '"Authorization code is invalid, expired or revoked","error":"invalid_grant"}')
    monkeypatch.setattr("schwab.auth.client_from_received_url", stale)

    assert reauth.main(["--finish", "https://127.0.0.1/?code=stale"]) == 1

    out = capsys.readouterr().out
    assert "the CODE ran out" in out
    assert "promptly" in out


def test_finish_does_not_blame_the_code_for_an_unrelated_failure(
        sandbox, monkeypatch, capsys):
    """A wrong app secret must NOT be reported as a slow paste."""
    _write_live_token(sandbox)
    reauth.main(["--begin"])

    def wrong_secret(*args, **kwargs):
        raise ValueError("invalid_client: 401 Unauthorized")
    monkeypatch.setattr("schwab.auth.client_from_received_url", wrong_secret)

    assert reauth.main(["--finish", "https://127.0.0.1/?code=abc"]) == 1
    assert "the CODE ran out" not in capsys.readouterr().out


def test_finish_restores_when_the_exchange_writes_a_bad_token(
        sandbox, monkeypatch, capsys):
    """Success reported by the library is not success until the file is read.

    The old script checked only that a file existed. This asserts the stronger
    check: a token that exists but cannot be used is rolled back.
    """
    _write_live_token(sandbox)
    before = sandbox.read_bytes()
    reauth.main(["--begin"])

    def writes_rubbish(api_key, app_secret, context, received_url, token_write_func):
        Path(config.SCHWAB_TOKEN_PATH).write_text(json.dumps({"nonsense": True}))

    monkeypatch.setattr("schwab.auth.client_from_received_url", writes_rubbish)

    assert reauth.main(["--finish", "https://127.0.0.1/?code=abc"]) == 1
    assert sandbox.read_bytes() == before
    assert "creation_timestamp" in capsys.readouterr().out


def test_finish_warns_when_the_parked_login_is_stale(sandbox, monkeypatch, capsys):
    reauth.main(["--begin"])
    path = reauth._context_path()
    parked = json.loads(path.read_text())
    parked["created_at"] = int(time.time()) - 60 * (reauth.CONTEXT_STALE_MINUTES + 5)
    path.write_text(json.dumps(parked))

    def exchange(api_key, app_secret, context, received_url, token_write_func):
        schwab.auth.TokenMetadata(
            FAKE_TOKEN, int(time.time()), token_write_func
        ).wrapped_token_write_func()(FAKE_TOKEN)

    monkeypatch.setattr("schwab.auth.client_from_received_url", exchange)

    assert reauth.main(["--finish", "https://127.0.0.1/?code=abc"]) == 0
    assert "minutes ago" in capsys.readouterr().out


# ─────────────────────────────────────────────────────────────────────────────
# --check, which must stay harmless
# ─────────────────────────────────────────────────────────────────────────────

def test_check_changes_nothing(sandbox, capsys):
    _write_live_token(sandbox)
    before = sandbox.read_bytes()

    assert reauth.main(["--check"]) == 0

    assert sandbox.read_bytes() == before
    assert not list(sandbox.parent.glob("*.bak-*"))
    assert "days remaining" in capsys.readouterr().out


def test_the_modes_are_mutually_exclusive():
    """--begin and --finish together is a mistake worth catching at the door."""
    with pytest.raises(SystemExit):
        reauth.main(["--begin", "--finish", "https://127.0.0.1/?code=abc"])
