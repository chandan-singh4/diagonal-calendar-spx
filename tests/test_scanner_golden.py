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

    actual = scanner["_compute_transform_scanner"](
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

    first = scanner["_compute_transform_scanner"](*args, put_offset=0, call_offset=0,
                                                  max_rows=50).reset_index(drop=True)
    second = scanner["_compute_transform_scanner"](*args, put_offset=0, call_offset=0,
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

    scanner["_compute_transform_scanner"](
        chain, float(meta["underlying_price"]), int(meta["snapshot_id"]),
        put_offset=0, call_offset=0, max_rows=50,
    )
    pd.testing.assert_frame_equal(before, chain)


@pytest.mark.parametrize("tag", SNAPSHOTS)
def test_scanner_respects_max_rows(tag):
    chain, _, meta = _load(tag)
    scanner = load_scanner_functions()
    out = scanner["_compute_transform_scanner"](
        chain, float(meta["underlying_price"]), int(meta["snapshot_id"]),
        put_offset=0, call_offset=0, max_rows=5,
    )
    assert len(out) <= 5


def test_scanner_handles_an_empty_chain():
    scanner = load_scanner_functions()
    out = scanner["_compute_transform_scanner"](pd.DataFrame(), 7400.0, 1)
    assert out.empty


@pytest.mark.parametrize("tag", SNAPSHOTS[:1])
def test_sweep_across_offsets_returns_a_superset_of_a_single_offset(tag):
    """_scan_all_offsets sweeps offsets so opportunities are not missed.

    Its whole justification is finding pairs that a single selected offset would
    hide, so the sweep must return at least as many distinct pairs as offset 0
    alone. If a refactor ever made the sweep narrower than one of its own
    members, that would defeat the feature silently.
    """
    chain, _, meta = _load(tag)
    scanner = load_scanner_functions()
    spx, sid = float(meta["underlying_price"]), int(meta["snapshot_id"])

    single = scanner["_compute_transform_scanner"](chain, spx, sid, 0, 0, max_rows=500)
    swept = scanner["_scan_all_offsets"](chain, spx, sid, max_rows_per_offset=500)

    assert len(swept) >= len(single)


def test_scanner_needs_no_database_or_streamlit():
    """The scanner must stay a pure function of (chain, price, ids).

    tests/app_loader.py executes it in a namespace containing no streamlit, no
    db and no config. That every test above runs is the proof — this test states
    the intent explicitly so the property is not lost by accident during M2,
    when the temptation to reach straight for db.* is highest.
    """
    scanner = load_scanner_functions()
    assert callable(scanner["_compute_transform_scanner"])
    assert callable(scanner["_scan_all_offsets"])