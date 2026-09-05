"""
Tests for schwab_client.py — the layer between this project and the broker.

WHY THIS MODULE (M1.7): everything the collector stores arrives through here,
and it was the last non-UI module at zero coverage. It is also the module most
exposed to a change nobody controls — Schwab can alter a field name or a
response shape without notice, and the failure would be silent: a renamed
`volatility` field yields None, rows get skipped as "illiquid", and collection
continues reporting healthy while storing progressively less.

WHAT IS BEING PROTECTED
  - The response SHAPE this code expects, written down as executable fixtures.
    If Schwab changes it, these fail loudly instead of the database quietly
    thinning out.
  - IV stays a PERCENTAGE here. The ÷100 to decimal happens in collector.py,
    deliberately, so app.py's legacy readers are unaffected. A conversion that
    drifted into this layer would double-apply.
  - VIX failure stays non-fatal, and quote failure stays fatal. Those are
    opposite on purpose.

NO NETWORK, NO TOKEN, NO PRODUCTION DATABASE. The client is a fake that returns
canned responses; the token tests write to pytest's tmp_path.
"""
from __future__ import annotations

import json
import time

import pandas as pd
import pytest
from conftest import FakeResponse, RecordingClient, make_raw_chain, quote_payload

import config
import schwab_client


# ─────────────────────────────────────────────────────────────────────────────
# Value sanitising
# ─────────────────────────────────────────────────────────────────────────────

class TestSafeFloat:

    @pytest.mark.parametrize("raw,expected", [
        (18.4, 18.4),
        ("18.4", 18.4),
        (7, 7.0),
    ])
    def test_usable_numbers_convert(self, raw, expected):
        assert schwab_client._safe_float(raw) == pytest.approx(expected)

    @pytest.mark.parametrize("raw", [None, "", "n/a", [], {}, float("nan")])
    def test_unusable_values_become_none(self, raw):
        assert schwab_client._safe_float(raw) is None

    def test_zero_becomes_none(self):
        """PINNING A DELIBERATE QUIRK, and it is worth understanding.

        Zero is treated as 'no value', not as the number zero. For Schwab that
        is usually right — an absent IV or an unquoted leg comes back as 0.0,
        and storing it as a real zero would drag every average down and look
        like data.

        But a deep out-of-the-money option genuinely CAN be bid 0.00, and that
        real zero is also discarded, leaving bid=None. Downstream this reads as
        'no quote' rather than 'quoted worthless'. It matches the settled rule
        (show nothing rather than zero) and is harmless for the near-the-money
        strikes this strategy trades, so it is left alone — but it is a real
        loss of information, and this test is where that is written down.
        """
        assert schwab_client._safe_float(0.0) is None
        assert schwab_client._safe_float(0) is None

    def test_nan_does_not_leak_through(self):
        """A NaN reaching the database would poison every average computed over
        it, and unlike None it would not be caught by a null check."""
        assert schwab_client._safe_float(float("nan")) is None


# ─────────────────────────────────────────────────────────────────────────────
# Quotes
# ─────────────────────────────────────────────────────────────────────────────

class TestSpxQuote:

    def test_mark_is_the_midpoint_when_both_sides_are_quoted(self):
        client = RecordingClient(quote_response=FakeResponse(
            quote_payload(config.UNDERLYING_SYMBOL,
                          bidPrice=5999.0, askPrice=6001.0, lastPrice=6000.5)))

        quote = schwab_client.get_spx_quote_full(client)

        assert quote["bid"] == 5999.0
        assert quote["ask"] == 6001.0
        assert quote["last"] == 6000.5
        assert quote["mark"] == pytest.approx(6000.0)

    def test_mark_falls_back_to_last_when_the_index_is_not_two_sided(self):
        """SPX is an index, not a traded security, so Schwab often publishes no
        bid or ask for it at all. Without this fallback the collector would
        raise 'no usable price' and lose the cycle on an ordinary response."""
        client = RecordingClient(quote_response=FakeResponse(
            quote_payload(config.UNDERLYING_SYMBOL, lastPrice=6000.5)))

        quote = schwab_client.get_spx_quote_full(client)

        assert quote["bid"] is None
        assert quote["ask"] is None
        assert quote["mark"] == 6000.5

    def test_one_sided_quotes_do_not_produce_a_half_midpoint(self):
        """A bid with no ask must NOT average to bid/2. Both sides or neither."""
        client = RecordingClient(quote_response=FakeResponse(
            quote_payload(config.UNDERLYING_SYMBOL,
                          bidPrice=5999.0, lastPrice=6000.5)))

        assert schwab_client.get_spx_quote_full(client)["mark"] == 6000.5

    def test_the_alternate_field_names_are_accepted(self):
        """Schwab has returned both `bidPrice`/`askPrice` and bare `bid`/`ask`
        depending on the endpoint."""
        client = RecordingClient(quote_response=FakeResponse(
            quote_payload(config.UNDERLYING_SYMBOL,
                          bid=5999.0, ask=6001.0, lastPrice=6000.5)))

        quote = schwab_client.get_spx_quote_full(client)

        assert quote["bid"] == 5999.0
        assert quote["ask"] == 6001.0

    def test_the_configured_symbol_is_requested(self):
        """`$SPX` is Schwab's convention. Requesting `SPX` returns a different
        instrument entirely, and the prices would look plausible."""
        client = RecordingClient(quote_response=FakeResponse(
            quote_payload(config.UNDERLYING_SYMBOL, lastPrice=6000.5)))

        schwab_client.get_spx_quote_full(client)

        assert client.quote_calls == [config.UNDERLYING_SYMBOL]
        assert config.UNDERLYING_SYMBOL.startswith("$")

    def test_an_http_failure_propagates(self):
        """A quote failure must be fatal to the cycle — a snapshot without an
        underlying price cannot be interpreted later."""
        client = RecordingClient(quote_response=FakeResponse(
            status_error=RuntimeError("401 Unauthorized")))

        with pytest.raises(RuntimeError):
            schwab_client.get_spx_quote_full(client)

    def test_the_simple_quote_helper_returns_the_last_price(self):
        client = RecordingClient(quote_response=FakeResponse(
            quote_payload(config.UNDERLYING_SYMBOL, lastPrice=6000.5)))

        assert schwab_client.get_spx_quote(client) == pytest.approx(6000.5)


class TestVixQuote:

    def test_the_vix_value_is_returned(self):
        client = RecordingClient(quote_response=FakeResponse(
            quote_payload(config.VIX_SYMBOL, lastPrice=18.64)))

        assert schwab_client.get_vix_quote(client) == pytest.approx(18.64)

    def test_a_failure_returns_none_instead_of_raising(self):
        """The opposite of the SPX quote, on purpose. VIX is context, not the
        product: losing it must not cost a snapshot of prices. This asymmetry
        is the single most important thing about this function."""
        client = RecordingClient(quote_response=FakeResponse(
            status_error=RuntimeError("500 Server Error")))

        assert schwab_client.get_vix_quote(client) is None

    def test_a_malformed_response_returns_none(self):
        """Non-fatal means non-fatal for a missing key too, not just HTTP."""
        client = RecordingClient(quote_response=FakeResponse({"unexpected": {}}))

        assert schwab_client.get_vix_quote(client) is None


# ─────────────────────────────────────────────────────────────────────────────
# Chain fetch
# ─────────────────────────────────────────────────────────────────────────────

class TestOptionChainFetch:

    def test_the_configured_strike_count_is_passed_through(self):
        """If this silently fell back to a Schwab default, the stored window
        would change size without anything in the code changing."""
        from datetime import date

        client = RecordingClient(chain_response=FakeResponse(make_raw_chain()))
        schwab_client.get_option_chain(client, date(2026, 7, 27),
                                       date(2026, 10, 25))

        call = client.chain_calls[0]
        assert call["symbol"] == config.UNDERLYING_SYMBOL
        assert call["strike_count"] == config.STRIKE_COUNT
        assert call["from_date"] == date(2026, 7, 27)
        assert call["to_date"] == date(2026, 10, 25)

    def test_an_http_failure_propagates(self):
        from datetime import date

        client = RecordingClient(chain_response=FakeResponse(
            status_error=RuntimeError("500 Server Error")))

        with pytest.raises(RuntimeError):
            schwab_client.get_option_chain(client, date(2026, 7, 27),
                                           date(2026, 10, 25))


# ─────────────────────────────────────────────────────────────────────────────
# Chain parsing — the shape most exposed to a change nobody controls
# ─────────────────────────────────────────────────────────────────────────────

class TestChainToDataFrame:

    def test_both_sides_are_flattened(self):
        df = schwab_client.chain_to_dataframe(make_raw_chain(
            expiries=[("2026-08-07", 7)], strikes=[6000.0]))

        assert set(df["side"]) == {"CALL", "PUT"}
        assert len(df) == 2

    def test_the_expiry_key_is_split_into_date_and_dte(self):
        """Schwab keys expiries as 'YYYY-MM-DD:DTE'. Storing the raw key as the
        expiry date would corrupt every date comparison downstream."""
        df = schwab_client.chain_to_dataframe(make_raw_chain(
            expiries=[("2026-08-07", 7)], strikes=[6000.0]))

        assert set(df["expiry"]) == {"2026-08-07"}
        assert set(df["dte"]) == {7}

    def test_strikes_are_numbers_not_the_string_keys(self):
        """Schwab keys strikes as strings. Left as text, '6000' sorts before
        '950' and every strike-window comparison silently misbehaves."""
        df = schwab_client.chain_to_dataframe(make_raw_chain(
            expiries=[("2026-08-07", 7)], strikes=[950.0, 6000.0]))

        assert df["strike"].dtype.kind == "f"
        assert sorted(df["strike"].unique()) == [950.0, 6000.0]

    def test_iv_stays_a_percentage_at_this_layer(self):
        """The ÷100 to decimal belongs to collector.py, deliberately, so
        app.py's legacy readers are unaffected. A conversion drifting into this
        layer would be applied twice and nothing would raise."""
        df = schwab_client.chain_to_dataframe(make_raw_chain(
            expiries=[("2026-08-07", 7)], strikes=[6000.0], iv=18.4))

        assert set(df["iv"]) == {18.4}

    def test_every_column_the_collector_reads_is_present(self):
        """The collector indexes these by name. A renamed column would raise
        deep inside a cycle rather than here."""
        df = schwab_client.chain_to_dataframe(make_raw_chain())

        for column in ("expiry", "dte", "strike", "side", "bid", "ask", "last",
                       "volume", "open_interest", "iv",
                       "delta", "gamma", "theta", "vega"):
            assert column in df.columns

    def test_an_empty_response_gives_an_empty_frame_not_a_crash(self):
        assert schwab_client.chain_to_dataframe({}).empty

    def test_a_missing_side_is_tolerated(self):
        """A half response is degraded, not fatal — the collector decides what
        to do with it."""
        raw = make_raw_chain(expiries=[("2026-08-07", 7)], strikes=[6000.0])
        del raw["putExpDateMap"]

        df = schwab_client.chain_to_dataframe(raw)

        assert set(df["side"]) == {"CALL"}


# ─────────────────────────────────────────────────────────────────────────────
# The broker's "no value" marker (BUG-030)
#
# Schwab answers -999.0 when it has nothing to give for a volatility or a greek.
# It was stored verbatim for ten weeks: 5,127 rows carry an IV of -9.99 (the
# marker after the collector's /100) and 5,081 rows carry each poisoned greek.
# Nothing raised, because -999.0 is a perfectly valid float.
#
# The trap these tests exist to hold shut is the OTHER direction. -9.99 is an
# entirely ordinary theta, and 38 rows in the real record legitimately hold it.
# A tolerance band, or a "anything below -100 is junk" rule, would quietly
# delete real prices while tidying up. Only the exact marker is a marker.
# ─────────────────────────────────────────────────────────────────────────────

def _poison(raw, field, value=schwab_client.SCHWAB_NO_VALUE):
    """Set `field` to the marker on every contract in a raw chain."""
    for side_key in ("callExpDateMap", "putExpDateMap"):
        for strikes in raw.get(side_key, {}).values():
            for contracts in strikes.values():
                for c in contracts:
                    c[field] = value
    return raw


class TestSchwabNoValueMarker:

    @pytest.mark.parametrize("field, column", [
        ("volatility", "iv"),
        ("delta",      "delta"),
        ("gamma",      "gamma"),
        ("theta",      "theta"),
        ("vega",       "vega"),
    ])
    def test_the_marker_becomes_a_blank_not_a_number(self, field, column):
        """Missing price -> blank, not 0 — and emphatically not -999."""
        raw = _poison(make_raw_chain(expiries=[("2026-08-07", 7)],
                                     strikes=[6000.0]), field)

        df = schwab_client.chain_to_dataframe(raw)

        assert df[column].isna().all()

    def test_a_theta_of_minus_nine_ninety_nine_is_real_data_and_survives(self):
        """The whole reason the comparison is exact. An option losing $9.99 a
        day is unremarkable, and -9.99 is only special AFTER the collector's
        /100 turns the marker into it — which happens to the IV alone."""
        raw = _poison(make_raw_chain(expiries=[("2026-08-07", 7)],
                                     strikes=[6000.0]), "theta", -9.99)

        df = schwab_client.chain_to_dataframe(raw)

        assert set(df["theta"]) == {-9.99}

    def test_quotes_are_left_alone(self):
        """bid/ask/last do not go through the filter. Schwab sends a real
        number or nothing at all for those, and a legitimate -999 quote is
        impossible only because prices cannot be negative — not a reason to
        add a filter that could one day misfire on a spread field."""
        raw = make_raw_chain(expiries=[("2026-08-07", 7)], strikes=[6000.0])

        df = schwab_client.chain_to_dataframe(raw)

        assert set(df["bid"]) == {9.0}
        assert set(df["ask"]) == {11.0}

    def test_ordinary_values_pass_through_untouched(self):
        """The half that matters: a filter that eats real greeks would show up
        as an empty column, and every other test here would still pass."""
        df = schwab_client.chain_to_dataframe(
            make_raw_chain(expiries=[("2026-08-07", 7)], strikes=[6000.0]))

        assert set(df["iv"]) == {18.4}
        assert set(df["delta"]) == {0.5}
        assert set(df["theta"]) == {-0.5}


class TestValueOrNone:
    """The filter itself, at the boundaries."""

    def test_the_marker_is_blanked(self):
        assert schwab_client._value_or_none(-999.0) is None

    def test_a_neighbouring_value_is_not(self):
        assert schwab_client._value_or_none(-998.9) == -998.9
        assert schwab_client._value_or_none(-999.1) == -999.1

    def test_missing_and_unparseable_are_blank(self):
        assert schwab_client._value_or_none(None) is None
        assert schwab_client._value_or_none("") is None
        assert schwab_client._value_or_none("n/a") is None

    def test_nan_is_blank(self):
        assert schwab_client._value_or_none(float("nan")) is None

    def test_zero_is_a_value(self):
        """Unlike _safe_float, which treats 0 as missing. A delta of 0.0 on a
        far out-of-the-money option is a fact, not an absence."""
        assert schwab_client._value_or_none(0.0) == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Strike window
# ─────────────────────────────────────────────────────────────────────────────

class TestStrikeWindowFilter:

    def _frame(self, strikes):
        return pd.DataFrame([
            {"strike": s, "side": "CALL", "iv": 18.0, "expiry": "2026-08-07",
             "dte": 7}
            for s in strikes
        ])

    def test_strikes_outside_the_window_are_dropped(self):
        df = self._frame([5600.0, 5700.0, 6000.0, 6300.0, 6400.0])

        kept = schwab_client.filter_chain_by_strike_window(df, 6000.0, width=300)

        assert sorted(kept["strike"]) == [5700.0, 6000.0, 6300.0]

    def test_the_boundary_is_inclusive(self):
        """Exactly ±width is inside. An exclusive boundary would quietly narrow
        the stored window by one strike at each end."""
        df = self._frame([5700.0, 6300.0])

        kept = schwab_client.filter_chain_by_strike_window(df, 6000.0, width=300)

        assert len(kept) == 2

    def test_an_empty_frame_passes_straight_through(self):
        empty = pd.DataFrame()

        assert schwab_client.filter_chain_by_strike_window(empty, 6000.0).empty

    def test_the_original_frame_is_not_modified(self):
        """The caller keeps using the unfiltered frame afterwards — the
        collector computes max_dte from it after filtering."""
        df = self._frame([5600.0, 6000.0])

        schwab_client.filter_chain_by_strike_window(df, 6000.0, width=300)

        assert len(df) == 2

    def test_the_expected_move_check_never_changes_what_is_stored(self, caplog):
        """The 2 SD check is informational. If it ever started filtering, the
        stored window would silently depend on volatility."""
        df = self._frame([5700.0, 6000.0, 6300.0])

        with_check = schwab_client.filter_chain_by_strike_window(
            df, 6000.0, width=300, atm_iv_pct=60.0, max_dte=90)
        without_check = schwab_client.filter_chain_by_strike_window(
            df, 6000.0, width=300)

        assert sorted(with_check["strike"]) == sorted(without_check["strike"])

    def test_a_wide_expected_move_is_warned_about(self, caplog):
        """A signal that the configured window is too narrow for the regime —
        strikes the strategy might need would be beyond it."""
        df = self._frame([6000.0])

        with caplog.at_level("WARNING"):
            schwab_client.filter_chain_by_strike_window(
                df, 6000.0, width=300, atm_iv_pct=60.0, max_dte=90)

        assert any("2 SD expected move" in r.message for r in caplog.records)

    def test_a_calm_market_produces_no_warning(self, caplog):
        """The counterpart to the test above. Without this, a check that warned
        unconditionally would still pass — and a warning that always fires is
        one nobody reads."""
        df = self._frame([6000.0])

        with caplog.at_level("WARNING"):
            schwab_client.filter_chain_by_strike_window(
                df, 6000.0, width=300, atm_iv_pct=8.0, max_dte=7)

        assert not any("2 SD expected move" in r.message
                       for r in caplog.records)


# ─────────────────────────────────────────────────────────────────────────────
# Token age — the 7-day clock
# ─────────────────────────────────────────────────────────────────────────────

class TestTokenAge:

    def _write_token(self, tmp_path, monkeypatch, payload):
        path = tmp_path / "token.json"
        path.write_text(json.dumps(payload))
        monkeypatch.setattr(config, "SCHWAB_TOKEN_PATH", str(path))
        return path

    def test_age_is_measured_from_the_creation_timestamp(self, tmp_path,
                                                          monkeypatch):
        """`creation_timestamp` is written at the interactive login and does NOT
        move on routine access-token refreshes. That is exactly why it tracks
        the 7-day refresh clock — a field that updated on every refresh would
        report the token as permanently new."""
        self._write_token(tmp_path, monkeypatch,
                          {"creation_timestamp": time.time() - 2 * 86400})

        assert schwab_client.get_token_age_days() == pytest.approx(2.0, abs=0.01)

    def test_a_missing_token_file_reports_unknown(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "SCHWAB_TOKEN_PATH",
                            str(tmp_path / "absent.json"))

        assert schwab_client.get_token_age_days() is None

    def test_unreadable_contents_report_unknown_rather_than_raising(
            self, tmp_path, monkeypatch):
        """This is called from the collector's startup path and from app.py. It
        must never be the reason either fails to start."""
        path = tmp_path / "token.json"
        path.write_text("{ not json")
        monkeypatch.setattr(config, "SCHWAB_TOKEN_PATH", str(path))

        assert schwab_client.get_token_age_days() is None

    def test_a_token_without_the_field_falls_back_to_the_file_time(
            self, tmp_path, monkeypatch):
        """Less precise, but better than reporting 'unknown' and losing the
        expiry warning entirely."""
        self._write_token(tmp_path, monkeypatch, {"access_token": "x"})

        age = schwab_client.get_token_age_days()

        assert age is not None
        assert age >= 0.0

    def test_the_age_crosses_the_seven_day_limit(self, tmp_path, monkeypatch):
        """Pairs with token_days_remaining() in collector.py, which turns this
        into the warning. Eight days old must read as past the limit."""
        self._write_token(tmp_path, monkeypatch,
                          {"creation_timestamp": time.time() - 8 * 86400})

        assert schwab_client.get_token_age_days() > 7.0


# ─────────────────────────────────────────────────────────────────────────────
# Which login path
# ─────────────────────────────────────────────────────────────────────────────

class TestClientAuthBranch:
    """The choice between reusing a cached token and starting a browser login.

    Worth pinning because the wrong branch is disruptive in a specific way: an
    unattended collector that takes the manual-flow branch will block forever
    waiting for a paste that nobody is there to give, during market hours.
    """

    def test_an_existing_token_is_reused_without_a_login(self, tmp_path,
                                                          monkeypatch):
        token = tmp_path / "token.json"
        token.write_text("{}")
        monkeypatch.setattr(config, "SCHWAB_TOKEN_PATH", str(token))
        monkeypatch.setattr(config, "SCHWAB_APP_KEY", "key")
        monkeypatch.setattr(config, "SCHWAB_APP_SECRET", "secret")

        calls = []
        monkeypatch.setattr(schwab_client.schwab.auth, "client_from_token_file",
                            lambda **kw: calls.append(("token_file", kw)) or "client")
        monkeypatch.setattr(schwab_client.schwab.auth, "client_from_manual_flow",
                            lambda **kw: calls.append(("manual", kw)) or "client")

        schwab_client.get_client()

        assert [c[0] for c in calls] == ["token_file"]

    def test_a_missing_token_starts_the_manual_login(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "SCHWAB_TOKEN_PATH",
                            str(tmp_path / "absent.json"))
        monkeypatch.setattr(config, "SCHWAB_APP_KEY", "key")
        monkeypatch.setattr(config, "SCHWAB_APP_SECRET", "secret")

        calls = []
        monkeypatch.setattr(schwab_client.schwab.auth, "client_from_token_file",
                            lambda **kw: calls.append(("token_file", kw)) or "client")
        monkeypatch.setattr(schwab_client.schwab.auth, "client_from_manual_flow",
                            lambda **kw: calls.append(("manual", kw)) or "client")

        schwab_client.get_client()

        assert [c[0] for c in calls] == ["manual"]

    def test_missing_credentials_fail_before_any_network_call(self, tmp_path,
                                                               monkeypatch):
        """config.validate() runs first on purpose, so an empty .env produces a
        clear message instead of a confusing auth error later."""
        monkeypatch.setattr(config, "SCHWAB_APP_KEY", "")
        monkeypatch.setattr(config, "SCHWAB_APP_SECRET", "")

        with pytest.raises(RuntimeError, match=r"Missing required \.env"):
            schwab_client.get_client()
