"""Transform-scanner maths — Phase A of Mission Control.

Pure pandas against a chain that is already in memory: no database, no page.
Pinned by tests/test_scanner_golden.py and tests/test_mc_pipeline_golden.py.

ON MEMOISATION (M2, ADR-032). In app.py `_compute_transform_scanner` carries
`@st.cache_data`, and two callers share those saved results: this module's
`_scan_all_offsets` sweep and the Scanner tab. Streamlit cannot be imported
here, so the decorator stays in app.py wrapping this function, and
`_scan_all_offsets` accepts the wrapped version through its `compute` argument.
Left to default it calls the uncached function directly — correct, and what the
golden tests exercise, but do NOT let production take that path: every sweep
would recompute all 21 offsets on every rerun.
"""
from __future__ import annotations

import pandas as pd

import iv_engine

_TSCAN_THRESHOLD  = 5.0
_APPROACHING_LOW  = 4.0   # gap in [4, 5) counts as "Approaching" (within 1 pt)
_SWEEP_OFFSETS    = list(range(0, 105, 5))   # symmetric put/call offsets, every 5 pts to 100
# Note: each offset is its own cache entry (keyed on snapshot_id + offset),
# so widening this list only adds cost the FIRST time each offset is hit for
# a given snapshot — it doesn't reintroduce per-interaction lag. Still bounded
# to symmetric (put_offset == call_offset) sweeps; a genuinely asymmetric
# combo (e.g. put -20 / call +45) can still fall outside this grid. The
# Calendar Edge backfill catches those the moment you investigate one manually.


def _compute_transform_scanner(
    _chain_df: pd.DataFrame,
    spx_price: float,
    snapshot_id: int,
    put_offset: int = 0,
    call_offset: int = 0,
    max_rows: int = 50,
) -> pd.DataFrame:
    """
    Batch version of Entry Analysis.

    For every valid expiry pair (back_DTE > front_DTE) find the nearest
    available strike to (ATM - put_offset) and (ATM + call_offset) that
    exists in BOTH expiries simultaneously, then compute:
        Diagonal Mark   = (back_call + back_put) - (front_call + front_put)
        Transform Mark  = (back_call + back_put) - (front_wing_call + front_wing_put)
        Transform Diff  = Transform Mark - Diagonal Mark

    Cached in app.py on (snapshot_id, put_offset, call_offset, max_rows) — the
    leading underscore on _chain_df tells Streamlit to skip hashing the
    DataFrame itself (expensive) and rely on snapshot_id as the real cache key,
    since snapshot_id fully determines chain_df's contents. Result only gets
    recomputed when a new snapshot actually lands, not on every tab click,
    widget interaction, or color-picker change. The underscore is therefore
    load-bearing even though nothing in this module reads it — see the module
    docstring before renaming it.
    """
    import bisect

    chain_df = _chain_df
    if chain_df.empty:
        return pd.DataFrame()

    expiries   = sorted(chain_df["expiry"].unique())
    dte_by_exp = chain_df.groupby("expiry")["dte"].first().to_dict()

    _cache: dict[tuple, tuple] = {}
    for (expiry, side), grp in chain_df.groupby(["expiry", "side"]):
        pairs = []
        for row in grp.itertuples(index=False):
            m = getattr(row, "mark", None)
            if m is None or (isinstance(m, float) and pd.isna(m)):
                bid = getattr(row, "bid", None)
                ask = getattr(row, "ask", None)
                if bid is not None and ask is not None:
                    try:
                        m = (float(bid) + float(ask)) / 2.0
                    except (TypeError, ValueError):
                        m = None
            if m is not None:
                try:
                    pairs.append((float(row.strike), float(m)))
                except (TypeError, ValueError):
                    pass
        if pairs:
            pairs.sort()
            _cache[(expiry, side)] = (
                [p[0] for p in pairs],
                [p[1] for p in pairs],
            )

    def _exact_mark(expiry: str, target: float, side: str):
        key = (expiry, side)
        if key not in _cache:
            return None
        strikes, marks = _cache[key]
        idx = bisect.bisect_left(strikes, target)
        if idx < len(strikes) and strikes[idx] == target:
            return marks[idx]
        return None

    def _nearest_common(exp1: str, exp2: str, target: float, side: str):
        key1, key2 = (exp1, side), (exp2, side)
        if key1 not in _cache or key2 not in _cache:
            return None, None, None
        common = sorted(set(_cache[key1][0]) & set(_cache[key2][0]))
        if not common:
            return None, None, None
        idx = bisect.bisect_left(common, target)
        if idx == 0:
            actual = common[0]
        elif idx == len(common):
            actual = common[-1]
        else:
            b, a = common[idx - 1], common[idx]
            actual = b if (target - b) <= (a - target) else a
        m1 = _exact_mark(exp1, actual, side)
        m2 = _exact_mark(exp2, actual, side)
        return actual, m1, m2

    atm_iv_cache: dict[str, float | None] = {
        exp: iv_engine.atm_iv(chain_df, exp, spx_price)
        for exp in expiries
    }

    atm_rounded  = round(spx_price / 5) * 5
    target_put   = float(atm_rounded - put_offset)
    target_call  = float(atm_rounded + call_offset)

    results = []

    for i, front in enumerate(expiries):
        front_dte = int(dte_by_exp.get(front, 0))
        if front_dte < 0:
            continue
        front_iv = atm_iv_cache.get(front)

        for back in expiries[i + 1:]:
            back_dte = int(dte_by_exp.get(back, 0))
            if back_dte <= front_dte:
                continue
            back_iv  = atm_iv_cache.get(back)
            iv_ratio = (
                round(front_iv / back_iv, 4)
                if (front_iv and back_iv and back_iv != 0) else None
            )

            put_s,  fp, bp = _nearest_common(front, back, target_put,  "PUT")
            call_s, fc, bc = _nearest_common(front, back, target_call, "CALL")

            if any(v is None for v in (put_s, call_s, fp, bp, fc, bc)):
                continue

            diag_mark = (bc + bp) - (fc + fp)

            wc = _exact_mark(front, call_s + 5, "CALL")
            wp = _exact_mark(front, put_s  - 5, "PUT")
            if wc is None or wp is None:
                continue

            transform_mark = (bc + bp) - (wc + wp)
            transform_diff = transform_mark - diag_mark

            results.append({
                "Front Expiry":   f"{front} ({front_dte}d)",
                "Back Expiry":    f"{back} ({back_dte}d)",
                "Put Strike":     int(put_s),
                "Call Strike":    int(call_s),
                "Diagonal Mark":  round(diag_mark,      2),
                "Transform Mark": round(transform_mark, 2),
                "Transform Diff": round(transform_diff, 2),
                "IV Ratio":       iv_ratio,
            })

    if not results:
        return pd.DataFrame()

    return (
        pd.DataFrame(results)
        .sort_values("Transform Diff", ascending=False)
        .head(max_rows)
        .reset_index(drop=True)
    )


def _scan_all_offsets(
    chain_df: pd.DataFrame,
    spx_price: float,
    snapshot_id: int,
    offsets: list[int] = _SWEEP_OFFSETS,
    max_rows_per_offset: int = 500,
    *,
    compute=None,
) -> pd.DataFrame:
    """
    Phase A — sweep a small set of symmetric put/call offsets across every
    valid expiry pair so opportunities aren't missed just because they sit
    outside whatever single offset is selected in the Scanner filter panel.

    Returns the union of all combos found, deduped on
    (Front Expiry, Back Expiry, Put Strike, Call Strike), sorted by
    Transform Diff descending. Front/Back Expiry columns retain the
    "YYYY-MM-DD (Nd)" format from _compute_transform_scanner — callers that
    need the raw date use .split(" ")[0].

    `compute` — the per-offset scanner to call. Production passes app.py's
    memoised wrapper so the 21 sweeps share saved results with the Scanner
    tab; see the module docstring. Defaults to the uncached function.
    """
    _compute = compute if compute is not None else _compute_transform_scanner
    frames = []
    for o in offsets:
        df = _compute(
            _chain_df=chain_df, spx_price=spx_price, snapshot_id=snapshot_id,
            put_offset=o, call_offset=o, max_rows=max_rows_per_offset,
        )
        if not df.empty:
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True)
    combined = combined.drop_duplicates(
        subset=["Front Expiry", "Back Expiry", "Put Strike", "Call Strike"]
    )
    return combined.sort_values("Transform Diff", ascending=False).reset_index(drop=True)
