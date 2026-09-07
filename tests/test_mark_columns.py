"""The three subtractions that define a diagonal, pinned in one place.

`core.scanner.add_mark_columns` collected four hand-written copies of these:
the Calendar Edge chart, the served card builder, and -- almost -- the React
rebuild. These checks exist because none of the copies would have FAILED if
they drifted; they would each have gone on returning a plausible number, and
two screens would have quietly disagreed about what a trade was worth.
"""
from __future__ import annotations

import pandas as pd

from core.scanner import add_mark_columns


def _row(**kw):
    base = dict(back_call_mark=0.0, back_put_mark=0.0,
                front_call_mark=0.0, front_put_mark=0.0,
                front_wing_call_mark=0.0, front_wing_put_mark=0.0)
    base.update(kw)
    return pd.DataFrame([base])


def test_the_diagonal_is_the_back_pair_less_the_front_pair():
    out = add_mark_columns(_row(back_call_mark=10.0, back_put_mark=9.0,
                                front_call_mark=4.0, front_put_mark=3.0))
    assert out["diagonal_mark"].iloc[0] == 12.0


def test_the_transform_is_the_back_pair_less_the_front_WINGS():
    """The same back leg sold against a different front. Using the front pair
    here instead of the wings would make the transform equal the diagonal and
    the gap identically zero -- which reads as "no opportunity anywhere",
    the most plausible-looking wrong answer this file can produce."""
    out = add_mark_columns(_row(back_call_mark=10.0, back_put_mark=9.0,
                                front_call_mark=4.0, front_put_mark=3.0,
                                front_wing_call_mark=2.0,
                                front_wing_put_mark=1.0))
    assert out["transform_mark"].iloc[0] == 16.0
    assert out["gap"].iloc[0] == 4.0


def test_the_frame_handed_in_is_not_modified():
    """Callers hold frames that came out of a cache. `SnapshotCache` returns
    the SAME object on every hit, so adding a column in place would change
    what every later reader sees."""
    df = _row(back_call_mark=10.0)
    add_mark_columns(df)
    assert "diagonal_mark" not in df.columns


def test_the_gap_column_is_called_gap():
    """The registry, the cards and the threshold all say `gap`. The Calendar
    Edge tab says `transform_gap` and renames it locally; that rename is the
    exception and is written down where it happens."""
    assert "gap" in add_mark_columns(_row()).columns
