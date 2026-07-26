"""
Load the scanner functions out of app.py without running the dashboard.

Same technique and same reasoning as tests/journal_loader.py: app.py is a
4,230-line Streamlit script whose module level queries the database and renders
six tabs. We want the scanner's arithmetic, not the page.

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

import iv_engine

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"

# Decorators that are safe to strip because they cannot change a return value.
_PURE_MEMOISERS = ("st.cache_data", "st.cache_resource")

_WANTED_FUNCS = ("_compute_transform_scanner", "_scan_all_offsets", "_break_sessions")

# Module-level constants the scanner reads (e.g. _SWEEP_OFFSETS default).
_WANTED_CONSTS = ("_SWEEP_OFFSETS",)


def _decorator_is_pure(node: ast.expr) -> bool:
    text = ast.unparse(node)
    return any(text.startswith(m) for m in _PURE_MEMOISERS)


def load_scanner_functions() -> dict:
    """Return {name: obj} for the scanner functions and their constants."""
    tree = ast.parse(APP_PATH.read_text(encoding="utf-8"), filename=str(APP_PATH))

    wanted_f, wanted_c = set(_WANTED_FUNCS), set(_WANTED_CONSTS)
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
            f"app.py no longer defines: {missing}. If the scanner moved (M2 "
            f"extraction), point the golden test at its new home — do NOT delete "
            f"the test, its whole purpose is to survive that move."
        )

    # Constants must be defined before the functions that default to them.
    picked.sort(key=lambda n: 0 if isinstance(n, ast.Assign) else 1)

    # iv_engine is itself pure and fully covered by test_iv_engine.py, so the
    # real module goes in — the scanner must be measured against the same
    # analytics the dashboard uses, not a stub.
    #
    # Deliberately absent: streamlit, db, config. The scanner must not need
    # them. A NameError naming one of those is a genuine finding (the scanner
    # reaching for I/O), not a gap in this list to be filled in.
    namespace: dict = {
        "pd": pd, "np": np, "math": math, "bisect": bisect,
        "iv_engine": iv_engine,
        "__builtins__": __builtins__,
    }

    module = ast.Module(body=picked, type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, filename=str(APP_PATH), mode="exec"), namespace)

    return {n: namespace[n] for n in (*_WANTED_FUNCS, *_WANTED_CONSTS)}
