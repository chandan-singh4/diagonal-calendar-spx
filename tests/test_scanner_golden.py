"""
Golden-output ("characterization") tests for the transform scanner.

WHAT THIS IS FOR
----------------
M2 breaks app.py apart and the scanner moves. A crash during that would be
obvious. The real risk is a SILENT change: the scanner comes out subtly
different, and weeks of trading decisions get made against a screen that
quietly shifted, with nothing to compare against.

These tests replay two real snapshots captured from the production database and
require the scanner to return exactly what it returned on 2026-07-26.

WHAT THIS DOES NOT CLAIM
------------------------
Nothing here says the scanner's answers are CORRECT. That question is open and
belongs to M6, where the strategy gets validated against logged results.
Asserting correctness now would mean inventing an expectation and freezing my
guess into the test suite, which is worse than no test: it would look like
validation while being nothing of the sort.

What these tests guarantee is narrower and actually true: whatever the scanner
does today, it will still do after the refactor.

IF ONE OF THESE FAILS DURING M2
-------------------------------
That is the test doing its job. The diff tells you which column moved and by
how much. Either the change was intended — in which case re-run
`scripts/capture_scanner_golden.py` and commit the diff as a deliberate,
reviewed change — or it was not, and you have just caught the exact class of
bug this file exists to catch. Re-capturing to make a red test go green throws
away the entire protection.

No database is opened here. The fixtures are self-contained.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from app_loader import load_scanner_functions

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "scanner"

SNAPSHOTS = sorted(p.name.replace("_meta.txt", "") for p in FIXTURE_DIR.glob("*_meta.txt"))


def _meta(tag: str) -> dict[str, str]:
    text = (FIXTURE_DIR / f"{tag}_meta.txt").read_text(encoding="utf-8")
    return dict(
        (k.strip(), v.strip())
        for k, _, v in (line.partition("=") for line in text.splitlines() if "=" in line)
    )


def _load(tag: str) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    chain = pd.read_csv(FIXTURE_DIR / f"{tag}_input.csv.gz")
    golden = pd.read_csv(FIXTURE_DIR / f"{tag}_scanner.csv.gz")
    return chain, golden, _meta(tag)


def test_fixtures_are_present():
    """A missing fixture must fail, not silently skip.

    Parametrizing over a glob means an empty directory would collect zero tests
    and report success — the safety net vanishing without a sound.
    """
    assert len(SNAPSHOTS) >= 2, (
        f"expected at least 2 captured snapshots, found {SNAPSHOTS}. "
        f"Run: python scripts/capture_scanner_golden.py"
    )


@pytest.mark.parametrize("tag", SNAPSHOTS)
def test_scanner_output_is_unchanged(tag):
    """The whole point: same input, same output, column for column."""
    chain, golden, meta = _load(tag)
    scanner = load_scanner_functions()

    actual = scanner["compute_transform_scanner"](
        chain,
        float(meta["underlying_price"]),
        int(meta["snapshot_id"]),
        put_offset=0, call_offset=0, max_rows=50,
    ).reset_index(drop=True)

    pd.testing.assert_frame_equal(
        golden, actual,
        check_dtype=False,   # CSV round-trip loses int64 vs float64 distinctions
        rtol=1e-9, atol=1e-9,
        obj=f"scanner output for {tag}",
    )


@pytest.mark.parametrize("tag", SNAPSHOTS)
def test_scanner_output_is_not_trivially_empty(tag):
    """Guards against the degenerate pass.

    An empty frame compared against an empty golden file would pass happily
    while proving nothing. These fixtures were captured with real rows, so
    demand real rows.
    """
    _, golden, _ = _load(tag)
    assert len(golden) > 0
    assert len(golden.columns) > 3


@pytest.mark.parametrize("tag", SNAPSHOTS)
def test_scanner_is_deterministic(tag):
    """Two runs over identical input must agree.

    Worth asserting separately: the scanner sorts and dedupes, and an unstable
    sort would make the golden comparison flap intermittently rather than fail
    honestly. This isolates that cause.
    """
    chain, _, meta = _load(tag)
    scanner = load_scanner_functions()
    args = (chain, float(meta["underlying_price"]), int(meta["snapshot_id"]))

    first = scanner["compute_transform_scanner"](*args, put_offset=0, call_offset=0,
                                                  max_rows=50).reset_index(drop=True)
    second = scanner["compute_transform_scanner"](*args, put_offset=0, call_offset=0,
                                                   max_rows=50).reset_index(drop=True)
    pd.testing.assert_frame_equal(first, second)


@pytest.mark.parametrize("tag", SNAPSHOTS)
def test_scanner_does_not_mutate_its_input(tag):
    """chain_df is shared with the rest of the dashboard.

    The scanner takes it as `_chain_df` and is memoised; if it mutated the
    frame, every other panel reading the same cached object would silently see
    the change. Pinning this now means M2 cannot introduce it unnoticed.
    """
    chain, _, meta = _load(tag)
    before = chain.copy(deep=True)
    scanner = load_scanner_functions()

    scanner["compute_transform_scanner"](
        chain, float(meta["underlying_price"]), int(meta["snapshot_id"]),
        put_offset=0, call_offset=0, max_rows=50,
    )
    pd.testing.assert_frame_equal(before, chain)


@pytest.mark.parametrize("tag", SNAPSHOTS)
def test_scanner_respects_max_rows(tag):
    chain, _, meta = _load(tag)
    scanner = load_scanner_functions()
    out = scanner["compute_transform_scanner"](
        chain, float(meta["underlying_price"]), int(meta["snapshot_id"]),
        put_offset=0, call_offset=0, max_rows=5,
    )
    assert len(out) <= 5


def test_scanner_handles_an_empty_chain():
    scanner = load_scanner_functions()
    out = scanner["compute_transform_scanner"](pd.DataFrame(), 7400.0, 1)
    assert out.empty


@pytest.mark.parametrize("tag", SNAPSHOTS[:1])
def test_sweep_across_offsets_returns_a_superset_of_a_single_offset(tag):
    """scan_all_offsets sweeps offsets so opportunities are not missed.

    Its whole justification is finding pairs that a single selected offset would
    hide, so the sweep must return at least as many distinct pairs as offset 0
    alone. If a refactor ever made the sweep narrower than one of its own
    members, that would defeat the feature silently.
    """
    chain, _, meta = _load(tag)
    scanner = load_scanner_functions()
    spx, sid = float(meta["underlying_price"]), int(meta["snapshot_id"])

    single = scanner["compute_transform_scanner"](chain, spx, sid, 0, 0, max_rows=500)
    swept = scanner["scan_all_offsets"](chain, spx, sid, max_rows_per_offset=500)

    assert len(swept) >= len(single)


# ─────────────────────────────────────────────────────────────────────────────
# The bid/ask midpoint fallback (DEBT-014, closed 2026-07-26)
#
# THE HOLE THIS FILLS. Mutation-testing the golden net on 2026-07-26 found that
# altering the midpoint formula inside compute_transform_scanner changed
# nothing — the net did not protect that branch at all. Both captured snapshots
# DO contain NULL-`mark` rows (77 of 3,096 in snapshot 2608), but none of them
# reach the top-50 output: the branch runs and its result is discarded. A
# characterization test can only protect what appears in its output.
#
# WHY SYNTHETIC RATHER THAN A THIRD CAPTURE. The backlog offered both. A capture
# would need the production database opened and a snapshot hunted for the right
# shape, and it would still only protect the branch for as long as that
# particular snapshot kept producing those particular rows. A built chain states
# the contract directly: these tests fail if the midpoint formula changes, and
# they say so in numbers checkable by hand.
#
# These assert real arithmetic, unlike the golden tests above, because the
# midpoint of a bid and an ask is not a matter of opinion.
# ─────────────────────────────────────────────────────────────────────────────

SPOT = 6000.0
FRONT_EXP, BACK_EXP = "2026-08-07", "2026-08-21"


def _leg(expiry, dte, strike, side, *, mark, bid, ask):
    """One contract row shaped like the dashboard's chain frame."""
    return {
        "expiry": expiry, "dte": dte, "strike": float(strike), "side": side,
        "mark": mark, "bid": bid, "ask": ask,
        # Present because the scanner's IV-ratio column needs them; held
        # constant so they cannot influence what these tests measure.
        "iv": 18.0, "theta": -0.5, "volume": 100, "open_interest": 1000,
        "delta": 0.5, "gamma": 0.01, "vega": 0.2, "last": 10.0,
    }


def _synthetic_chain(*, front_mark, front_bid, front_ask,
                     back_mark=25.0, back_bid=24.0, back_ask=26.0):
    """Two expiries, strikes 5980–6020 in 5s, both sides.

    Wide enough that the ±5 wing strikes the Transform Mark needs exist, so the
    scanner produces a row rather than declining for want of a wing.
    """
    rows = []
    for expiry, dte, mark, bid, ask in (
        (FRONT_EXP, 7, front_mark, front_bid, front_ask),
        (BACK_EXP, 21, back_mark, back_bid, back_ask),
    ):
        for offset in (-20, -15, -10, -5, 0, 5, 10, 15, 20):
            for side in ("CALL", "PUT"):
                rows.append(_leg(expiry, dte, SPOT + offset, side,
                                 mark=mark, bid=bid, ask=ask))
    return pd.DataFrame(rows)


def _scan(chain):
    return load_scanner_functions()["compute_transform_scanner"](
        chain, SPOT, 1, put_offset=0, call_offset=0, max_rows=50)


class TestBidAskMidpointFallback:

    def test_the_midpoint_is_used_when_mark_is_missing(self):
        """The DEBT-014 test. Every front leg here has NO mark, so its price can
        only come from the midpoint of 9.00 and 11.00.

            Diagonal Mark = (back_call + back_put) - (front_call + front_put)
                          = (25 + 25) - (10 + 10)
                          = 30.00

        Change the formula and this number moves: taking the bid alone gives 32,
        the ask alone 28, a mean of three 33.33. There is no way to alter the
        midpoint and still land on 30.
        """
        out = _scan(_synthetic_chain(front_mark=None, front_bid=9.0,
                                     front_ask=11.0))

        assert len(out) == 1
        assert out.iloc[0]["Diagonal Mark"] == pytest.approx(30.0)

    def test_an_explicit_mark_wins_over_the_midpoint(self):
        """The fallback must be a fallback, not an override.

        Back legs carry mark=30.00 while their bid/ask midpoint is 25.00, so the
        two answers differ on purpose:

            using the stored mark → (30 + 30) - (10 + 10) = 40.00
            using the midpoint    → (25 + 25) - (10 + 10) = 30.00

        A stored mark is the broker's own valuation; recomputing over the top of
        it would silently discard it.
        """
        out = _scan(_synthetic_chain(front_mark=None, front_bid=9.0,
                                     front_ask=11.0, back_mark=30.0,
                                     back_bid=24.0, back_ask=26.0))

        assert out.iloc[0]["Diagonal Mark"] == pytest.approx(40.0)

    def test_a_leg_with_no_price_at_all_yields_no_number_not_a_zero(self):
        """No mark and no quotes means no price.

        The pair is still LISTED — it exists — but its three money columns come
        back empty rather than filled in. That is the scanner's half of the
        settled rule: show nothing rather than zero.

        Zero would be the dangerous outcome. A front leg valued at 0.00 makes
        the Diagonal Mark 50.00 instead of blank, which is not a small error —
        it is the largest number on the screen, sorted to the very top, and it
        describes legs that cannot actually be traded.

        (Written expecting the row to be dropped entirely; it is kept with empty
        values instead. Pinning what the code really does, which is the safer of
        the two: a listed pair with no price is visible and obviously
        incomplete, whereas a silently missing pair is not.)
        """
        out = _scan(_synthetic_chain(front_mark=None, front_bid=None,
                                     front_ask=None))

        assert len(out) == 1
        row = out.iloc[0]
        for column in ("Diagonal Mark", "Transform Mark", "Transform Diff"):
            assert pd.isna(row[column]), f"{column} should be empty, got {row[column]}"
            assert row[column] != 0

    def test_a_one_sided_quote_does_not_produce_a_half_midpoint(self):
        """A bid with no ask cannot be averaged.

        The specific failure guarded against: `(bid + 0) / 2` — pricing the leg
        at 4.50 instead of leaving it empty, which makes the diagonal look 11
        points cheaper than anything real.
        """
        out = _scan(_synthetic_chain(front_mark=None, front_bid=9.0,
                                     front_ask=None))

        assert pd.isna(out.iloc[0]["Diagonal Mark"])

    def test_an_unparseable_quote_does_not_raise(self):
        """The branch guards float() with try/except. Real chains have carried
        empty strings in place of numbers, and the scanner must survive that —
        a raise here takes down the whole tab, not one row.

        NOTE — two paths, two different outcomes, both safe. An unparseable
        quote makes the leg unusable, so the pair never forms and the table is
        empty. A missing quote (None) survives as NaN through pandas and
        produces a listed pair with empty money columns instead. Same
        underlying condition, 'no usable price', reported two ways.

        Neither fabricates a number, so neither is a defect worth changing
        during M1 — but the inconsistency is real, and a future reader comparing
        these two tests deserves to know it is understood rather than
        accidental.
        """
        out = _scan(_synthetic_chain(front_mark=None, front_bid="n/a",
                                     front_ask="n/a"))

        assert out.empty

    def test_the_transform_mark_also_depends_on_the_fallback(self):
        """The wing legs are front-expiry too, so the Transform Mark — the
        number the whole screen is built to compare — is computed from midpoints
        as well:

            (back_call + back_put) - (front_wing_call + front_wing_put)
                = (25 + 25) - (10 + 10) = 30.00
        """
        out = _scan(_synthetic_chain(front_mark=None, front_bid=9.0,
                                     front_ask=11.0))

        assert out.iloc[0]["Transform Mark"] == pytest.approx(30.0)


def test_scanner_needs_no_database_or_streamlit():
    """The scanner must stay a pure function of (chain, price, ids).

    tests/app_loader.py executes it in a namespace containing no streamlit, no
    db and no config. That every test above runs is the proof — this test states
    the intent explicitly so the property is not lost by accident during M2,
    when the temptation to reach straight for db.* is highest.
    """
    scanner = load_scanner_functions()
    assert callable(scanner["compute_transform_scanner"])
    assert callable(scanner["scan_all_offsets"])
