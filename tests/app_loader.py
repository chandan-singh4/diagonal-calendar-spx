"""
Load pure functions out of app.py without running the dashboard.

Same technique and same reasoning as tests/journal_loader.py: app.py is a
~4,300-line Streamlit script whose module level queries the database and renders
six tabs. We want its arithmetic, not the page.

TWO ENTRY POINTS, both built on _load_from_app():

  load_scanner_functions() — the transform scanner (test_scanner_golden.py)
  load_display_functions() — the layer that decides what reaches the screen:
                             card ordering, chart geometry, cell formatting
                             (test_display_golden.py)

Each caller passes its OWN namespace. That is the point, not an accident: the
scanner is given no plotly and no I/O, so a scanner that starts reaching for
either fails loudly here. Widening one caller's namespace must never widen
another's.

ONE EXTRA WRINKLE vs. the journal loader: the scanner carries
@st.cache_data(...). That decorator is a memoisation wrapper — it changes when
the function runs, never what it returns for given arguments. Stripping it is
therefore safe for a characterization test, and necessary, since applying it
outside a Streamlit runtime is meaningless. The strip is explicit and asserted
below so that a decorator which DOES affect results cannot slip through
unnoticed.
"""
from __future__ import annotations

import ast
import bisect
import math
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go

import config
import db
import iv_engine

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"

# Decorators that are safe to strip because they cannot change a return value.
_PURE_MEMOISERS = ("st.cache_data", "st.cache_resource")

_WANTED_FUNCS = ("_compute_transform_scanner", "_scan_all_offsets", "_break_sessions")

# Module-level constants the scanner reads (e.g. _SWEEP_OFFSETS default).
_WANTED_CONSTS = ("_SWEEP_OFFSETS",)

# The display layer: everything between a computed number and your eyes.
_DISPLAY_FUNCS = (
    "_rank_for_panel",        # the ORDER cards appear in
    "_banded_ratio_traces",   # the multicolor IV-ratio line's geometry
    "_sparkline",             # the ▁▂▃ trend glyph on every card
    "_fmt_duration",          # "2h 12m"
    "_fmt_eta",               # "~18 min"
    "_card_key",              # a card's identity across reruns
)
_DISPLAY_CONSTS = ("_SPARK_BARS", "_RATIO_THRESHOLDS", "_RATIO_BANDS")

# The Mission Control layer — reads the database, so unlike the two above it
# needs db and config in its namespace. See DEBT-026 / ADR-030.
_MC_FUNCS = ("_candidate_signals", "_sparkline")
_MC_CONSTS = ("_SPARK_BARS", "_TSCAN_THRESHOLD")


def _decorator_is_pure(node: ast.expr) -> bool:
    text = ast.unparse(node)
    return any(text.startswith(m) for m in _PURE_MEMOISERS)


def _load_from_app(funcs: tuple[str, ...], consts: tuple[str, ...],
                   namespace: dict, *, what: str) -> dict:
    """Extract `funcs` and `consts` from app.py and exec them in `namespace`.

    `what` names the caller in failure messages, so a missing function tells you
    which test to go and repoint rather than just which name vanished.
    """
    tree = ast.parse(APP_PATH.read_text(encoding="utf-8"), filename=str(APP_PATH))

    wanted_f, wanted_c = set(funcs), set(consts)
    picked: list[ast.stmt] = []

    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if names & wanted_c:
                picked.append(node)
                wanted_c -= names

        elif isinstance(node, ast.FunctionDef) and node.name in wanted_f:
            for dec in node.decorator_list:
                if not _decorator_is_pure(dec):
                    raise AssertionError(
                        f"{node.name}() carries decorator @{ast.unparse(dec)}, which is "
                        f"not a known-pure memoiser. Stripping it could change results. "
                        f"Review tests/app_loader.py before proceeding."
                    )
            node.decorator_list = []  # safe: memoisation only
            picked.append(node)
            wanted_f.discard(node.name)

    missing = sorted(wanted_f | wanted_c)
    if missing:
        raise AssertionError(
            f"app.py no longer defines: {missing}. If the {what} moved (M2 "
            f"extraction), point the golden test at its new home — do NOT delete "
            f"the test, its whole purpose is to survive that move."
        )

    # Constants must be defined before the functions that default to them.
    picked.sort(key=lambda n: 0 if isinstance(n, ast.Assign) else 1)

    module = ast.Module(body=picked, type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, filename=str(APP_PATH), mode="exec"), namespace)

    return {n: namespace[n] for n in (*funcs, *consts)}


def load_scanner_functions() -> dict:
    """Return {name: obj} for the scanner functions and their constants."""
    # iv_engine is itself pure and fully covered by test_iv_engine.py, so the
    # real module goes in — the scanner must be measured against the same
    # analytics the dashboard uses, not a stub.
    #
    # Deliberately absent: streamlit, db, config, plotly. The scanner must not
    # need them. A NameError naming one of those is a genuine finding (the
    # scanner reaching for I/O or drawing), not a gap in this list to be
    # filled in.
    namespace: dict = {
        "pd": pd, "np": np, "math": math, "bisect": bisect,
        "iv_engine": iv_engine,
        "__builtins__": __builtins__,
    }
    return _load_from_app(_WANTED_FUNCS, _WANTED_CONSTS, namespace, what="scanner")


def load_display_functions() -> dict:
    """Return {name: obj} for the display layer and its constants.

    plotly IS supplied here, unlike the scanner namespace, because
    _banded_ratio_traces legitimately builds Scatter objects — that is the thing
    under test. Still deliberately absent: streamlit, db, config. Anything in
    this layer that reaches for the database or the page is misplaced, and a
    NameError here is the finding.
    """
    namespace: dict = {
        "pd": pd, "np": np, "math": math, "go": go,
        "__builtins__": __builtins__,
    }
    return _load_from_app(_DISPLAY_FUNCS, _DISPLAY_CONSTS, namespace, what="display layer")


def load_mission_control_functions() -> dict:
    """Return {name: obj} for the DB-reading Mission Control layer.

    `db` and `config` ARE supplied here — this layer legitimately reads
    snapshots. Note what that costs: `_candidate_signals` calls
    `db.get_transform_mark_history(config.DB_PATH, ...)`, taking the database
    path from a module global rather than an argument. A test therefore cannot
    simply pass it a temporary database; it has to monkeypatch `config.DB_PATH`.
    That is a genuine testability defect, not a quirk of this loader, and it is
    exactly the kind of hidden dependency M2 should turn into a parameter.
    Recorded as DEBT-027 so the eventual fix is deliberate.

    Still deliberately absent: streamlit. Nothing in this layer may touch the
    page or session state — the real `_candidate_signals` is careful about that
    because it runs inside a cache boundary, and a NameError here would be the
    finding if that ever changed.
    """
    namespace: dict = {
        "pd": pd, "np": np, "math": math,
        "db": db, "config": config,
        "__builtins__": __builtins__,
    }
    return _load_from_app(_MC_FUNCS, _MC_CONSTS, namespace, what="Mission Control layer")
