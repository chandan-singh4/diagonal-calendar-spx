"""M4.5 — the token, and the binding that makes it worth having.

WHAT IS BEING PROTECTED. Not a secret: Chandan's own read-only view of his own
data. What a stray device on the network should not be able to do is fetch the
whole price history by guessing a port — and, once M4.5's tunnel is in use,
what the tunnel alone should not be the only thing standing in the way.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api import auth
from api.app import create_app

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def token_env(monkeypatch):
    monkeypatch.setenv(auth.TOKEN_ENV_VAR, "a-real-token")
    return "a-real-token"


def test_the_api_is_open_when_no_token_is_configured(temp_db, monkeypatch):
    """The correct shape for the machine it runs on: bound to localhost, with
    the dashboard beside it that has never asked for a password either."""
    monkeypatch.delenv(auth.TOKEN_ENV_VAR, raising=False)

    assert TestClient(create_app(db_path=temp_db)).get("/health").status_code == 200


def test_an_empty_token_counts_as_no_token(monkeypatch):
    """`SPX_API_TOKEN=` is a line someone wrote while turning this OFF."""
    monkeypatch.setenv(auth.TOKEN_ENV_VAR, "   ")

    assert auth.configured_token() is None


def test_a_request_without_the_token_is_refused(temp_db, token_env):
    response = TestClient(create_app(db_path=temp_db)).get("/snapshots/latest")

    assert response.status_code == 401


def test_a_request_with_the_wrong_token_is_refused(temp_db, token_env):
    client = TestClient(create_app(db_path=temp_db))

    response = client.get("/snapshots/latest",
                          headers={auth.HEADER: "not-the-token"})

    assert response.status_code == 401


def test_a_request_with_the_token_is_allowed(temp_db, token_env):
    client = TestClient(create_app(db_path=temp_db))

    response = client.get("/snapshots/latest",
                          headers={auth.HEADER: token_env})

    assert response.status_code == 200


def test_health_stays_reachable_without_the_token(temp_db, token_env):
    """So a monitor can confirm the server is up without holding the secret.
    It publishes no market data beyond a snapshot id, a timestamp and spot."""
    assert TestClient(create_app(db_path=temp_db)).get("/health").status_code == 200


def test_the_refusal_does_not_say_which_way_it_failed(temp_db, token_env):
    """"No token" and "wrong token" are different facts, and telling them
    apart helps whoever is guessing more than whoever forgot a header."""
    client = TestClient(create_app(db_path=temp_db))

    missing = client.get("/snapshots/latest").json()["detail"]
    wrong = client.get("/snapshots/latest",
                       headers={auth.HEADER: "nope"}).json()["detail"]

    assert missing == wrong


def test_every_route_is_covered_not_just_the_decorated_ones(temp_db, token_env):
    """The middleware exists so a route added later is closed by default.

    A per-route dependency protects the routes someone remembered to decorate;
    the one they forget is open, and nothing says so.
    """
    client = TestClient(create_app(db_path=temp_db))
    app = create_app(db_path=temp_db)

    # Taken from the OpenAPI schema rather than app.routes: included routers
    # are wrapped rather than flattened, so walking app.routes finds the four
    # built-in doc endpoints and nothing else — an assertion that would have
    # passed vacuously over zero real routes.
    paths = [p for p in app.openapi()["paths"]
             if "get" in app.openapi()["paths"][p] and p != "/health"]
    assert len(paths) >= 10, (
        f"only {len(paths)} routes found — the reads and computed routers "
        f"should both be present, so this is not checking what it claims"
    )

    for path in paths:
        # Unfilled path params would 404 before reaching auth; none of the
        # current routes have any, and this asserts that stays true.
        assert "{" not in path, f"{path} takes a path parameter — check it too"
        assert client.get(path).status_code == 401, f"{path} is unprotected"


def test_the_token_is_compared_in_constant_time():
    """Pinned to the source. `==` returns at the first differing byte, so how
    long it takes leaks how much of the token was right — recoverable one
    character at a time over enough attempts."""
    source = (ROOT / "api" / "auth.py").read_text(encoding="utf-8")

    assert "hmac.compare_digest" in source
    assert not re.search(r"supplied\s*==\s*expected", source)


# ─────────────────────────────────────────────────────────────────────────────
# OPS-006 — the binding
# ─────────────────────────────────────────────────────────────────────────────

def test_streamlit_binds_localhost_only():
    """OPS-006, open since 2026-07-26 and confirmed true then: `streamlit run`
    advertised a Network URL and an External URL, so the dashboard was
    reachable from any device on the LAN. ADR-012 makes localhost-only a hard
    requirement before any further exposure."""
    config = (ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8")

    assert re.search(r'^\s*address\s*=\s*"127\.0\.0\.1"', config, re.MULTILINE), (
        "Streamlit must bind 127.0.0.1 — Tailscale reaches this interface, so "
        "binding the LAN as well widens who can connect and enables nothing"
    )
