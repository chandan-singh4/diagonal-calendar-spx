"""DataFrame to JSON, without inventing anything.

THE RULE THIS FILE EXISTS TO KEEP: missing price → blank, never 0.

It is a project rule of long standing and it is easy to break here without
noticing, because every convenient path breaks it. `df.fillna(0)` breaks it
outright. `df.to_json()` emits bare `NaN`, which is not JSON and which several
parsers silently read as 0. `float(numpy.nan)` survives all the way to
`json.dumps`, which writes `NaN` unless told otherwise. So the conversion is
done explicitly, once, here.

The distinction is not pedantry in this domain. A missing option price means
the broker returned nothing for that contract; a price of 0 means the market
says it is worthless. On a long leg those are opposite conclusions, and the
gap between them is the entire trade.

TIMESTAMPS LEAVE ZONED. `dataaccess/` returns zoned UTC exactly as stored
(ADR-038, closing DEBT-030) and this layer keeps it that way, in ISO 8601 with
the offset present. The page converts to market time at the last moment before
drawing because Plotly needs a naive value; a JSON client is not a chart and
must not be handed a bare wall-clock with nothing saying where.
"""
from __future__ import annotations

import datetime as _dt
import math
from typing import Any

import numpy as np
import pandas as pd


def scrub(value: Any) -> Any:  # noqa: PLR0911 — a type dispatch is a chain of returns
    """One value, made safe for JSON — with absence preserved as null.

    Handles the three ways a missing number arrives from pandas/numpy: the
    float NaN, pandas' NA/NaT sentinels, and numpy's own scalar types, which
    are not instances of the Python builtins and which `json` cannot encode.
    """
    if value is None:
        return None
    # pd.isna on an array raises rather than returning a scalar, so anything
    # array-shaped is handled before it gets here (see frame_to_records).
    if isinstance(value, float) and math.isnan(value):
        return None
    if value is pd.NaT or value is pd.NA:
        return None
    if isinstance(value, (pd.Timestamp, _dt.datetime)):
        if pd.isna(value):
            return None
        # A naive timestamp reaching here is a bug upstream, not something to
        # paper over with an assumed zone: dataaccess/ returns zoned UTC. It is
        # emitted as-is so the missing offset is visible to whoever reads it,
        # rather than being quietly stamped with a zone nobody chose.
        return value.isoformat()
    if isinstance(value, _dt.date):
        return value.isoformat()
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        as_float = float(value)
        return None if math.isnan(as_float) else as_float
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.ndarray):
        return [scrub(v) for v in value.tolist()]
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [scrub(v) for v in value]
    return value


def frame_to_records(df: pd.DataFrame | None) -> list[dict[str, Any]]:
    """A DataFrame as a list of JSON-safe row dicts. Empty frame → empty list.

    An empty result is `[]`, not an error and not a row of zeros. "No rows"
    is a real and frequent answer here — before the first session of a
    contract, or on a strike that did not trade — and the caller is entitled
    to tell that apart from "rows exist and every value is zero".
    """
    if df is None or df.empty:
        return []

    # `.where(notna, None)` rather than fillna: it replaces only the missing
    # values and leaves every real 0 alone, which is the whole point.
    safe = df.astype(object).where(pd.notna(df), None)
    return [
        {str(col): scrub(val) for col, val in row.items()}
        for row in safe.to_dict(orient="records")
    ]


def frame_payload(df: pd.DataFrame | None, **meta: Any) -> dict[str, Any]:
    """The standard response shape for a read: rows, a count, and context.

    The count is published because `[]` alone cannot distinguish "the query
    ran and found nothing" from "something upstream returned early". Anything
    passed as `meta` — the expiry asked for, the snapshot it came from — is
    echoed back so a stored response can be interpreted later without the
    request beside it.
    """
    records = frame_to_records(df)
    return {"count": len(records), **{k: scrub(v) for k, v in meta.items()},
            "rows": records}
