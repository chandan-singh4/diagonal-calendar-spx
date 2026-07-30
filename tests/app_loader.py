"""
Load pure functions out of app.py and core/ without running the dashboard.

Same technique and same reasoning as tests/journal_loader.py: app.py is a
Streamlit script whose module level queries the database and renders six tabs.
We want its arithmetic, not the page.

TWO SOURCES SINCE M2 (ADR-032). The pure calculations now live in core/, and
this loader searches core/ FIRST, then app.py. Two consequences worth knowing:

  * A name that moved to core/ needs no change here — the golden tests keep
    passing across the move, which is the whole point of having written them
    before it.
  * app.py may legitimately hold a same-named BINDING that wraps the core
    function in a Streamlit memo (`_compute_transform_scanner`). That is an
    assignment, not a definition, so it is never picked up — the tests measure
    the real function, not the wrapper.

A name DEFINED in two sources is rejected outright: that is a half-finished
move, and silently preferring core/ would let the tests pass against code the
dashboard no longer runs.

Loading through the AST rather than importing core/ directly is deliberate and
still earns its keep: each caller supplies its own namespace, so a core
function that starts reaching for the database or the page raises NameError
here instead of passing quietly. tests/test_core_layering.py enforces the same
rule from the other side, on the import list.

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
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go

import config
import db
import iv_engine

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_PATH = REPO_ROOT / "app.py"
CORE_DIR = REPO_ROOT / "core"

# core/ first — see the module docstring. Sorted so the search order is stable
# and a failure message names the same file every run.
SOURCES: tuple[Path, ...] = (*sorted(CORE_DIR.glob("*.py")), APP_PATH)

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

# The FULL Mission Control pipeline plus the query wrappers — everything left in
# DEBT-026 after _candidate_signals. Needs db, config, json, Path and a
# session_state stand-in. See ADR-031.
_PIPELINE_FUNCS = (
    # scanner, because _scan_all_offsets is a real collaborator of the core
    "_compute_transform_scanner", "_scan_all_offsets",
    # display helpers the pipeline calls directly
    "_rank_for_panel", "_card_key", "_sparkline", "_fmt_duration", "_exp_label",
    # signals
    "_candidate_signals",
    # the persisted eligibility registry
    "_load_eligible_history", "_save_eligible_history", "_update_eligible_history",
    # the pipeline itself
    "_compute_mc_core", "_build_non_atm_panel", "_run_mission_control",
    # the query wrappers
    "_load_atm_hist", "_load_atm_hist_fb", "_load_contract_hist", "_load_chain_df",
    "_load_spx_intraday", "_load_prior_close", "_load_transform_marks",
    "_load_latest_atm_iv", "_load_diagonal_hist",
)
_PIPELINE_CONSTS = (
    "_SWEEP_OFFSETS", "_TSCAN_THRESHOLD", "_APPROACHING_LOW", "_MC_HISTORY_CAP",
    "_SPARK_BARS", "_ELIGIBLE_HISTORY_PATH", "_ELIGIBLE_HISTORY_RETENTION_DAYS",
)


class FakeSessionState(dict):
    """Stand-in for st.session_state — a dict with .get(), which is all
    _run_mission_control uses of it.

    Deliberately a real dict rather than a mock: the "New" badge logic depends on
    values PERSISTING across calls, and that persistence is the thing under test.
    A mock that recorded calls would prove nothing about whether a card correctly
    stops being new on the second look at the same snapshot.
    """


class FakeStreamlit:
    """The only Streamlit surface the pipeline touches at runtime.

    Cache decorators are stripped by the loader, so nothing else is needed. If
    this ever has to grow a method, that is a finding: it means logic has started
    reaching for the page from inside the data layer.
    """

    def __init__(self):
        self.session_state = FakeSessionState()


def _decorator_is_pure(node: ast.expr) -> bool:
    text = ast.unparse(node)
    return any(text.startswith(m) for m in _PURE_MEMOISERS)


def _definitions(funcs: set[str], consts: set[str]) -> dict[str, list[tuple[Path, ast.stmt]]]:
    """Every top-level definition of a wanted name, across every source.

    Returns name -> [(source, node), ...]. More than one entry for a name is a
    half-finished move, and the caller rejects it. Assignments only count for
    `consts`, which is what lets app.py keep a memo-wrapping binding under the
    same name as a core function without shadowing it here.
    """
    found: dict[str, list[tuple[Path, ast.stmt]]] = {}

    for src in SOURCES:
        tree = ast.parse(src.read_text(encoding="utf-8"), filename=str(src))
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in consts:
                        found.setdefault(target.id, []).append((src, node))
            elif isinstance(node, ast.FunctionDef) and node.name in funcs:
                found.setdefault(node.name, []).append((src, node))

    return found


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _load_from_app(funcs: tuple[str, ...], consts: tuple[str, ...],
                   namespace: dict, *, what: str) -> dict:
    """Extract `funcs` and `consts` from core/ + app.py and exec in `namespace`.

    `what` names the caller in failure messages, so a missing function tells you
    which test to go and repoint rather than just which name vanished.
    """
    wanted_f, wanted_c = set(funcs), set(consts)
    found = _definitions(wanted_f, wanted_c)

    missing = sorted((wanted_f | wanted_c) - found.keys())
    if missing:
        raise AssertionError(
            f"No source in {[_rel(s) for s in SOURCES]} defines: {missing}. If "
            f"the {what} moved (M2 extraction), point the golden test at its new "
            f"home — do NOT delete the test, its whole purpose is to survive "
            f"that move."
        )

    duplicated = {n: [_rel(s) for s, _ in hits] for n, hits in found.items() if len(hits) > 1}
    if duplicated:
        raise AssertionError(
            f"Defined in more than one place: {duplicated}. That is a "
            f"half-finished extraction — the dashboard runs one copy and this "
            f"loader would silently test the other. Delete the stale one."
        )

    picked: list[tuple[Path, ast.stmt]] = []
    seen: set[int] = set()
    for name in (*consts, *funcs):
        src, node = found[name][0]
        if id(node) in seen:      # one `a = b = ...` can define two wanted names
            continue
        seen.add(id(node))
        if isinstance(node, ast.FunctionDef):
            for dec in node.decorator_list:
                if not _decorator_is_pure(dec):
                    raise AssertionError(
                        f"{node.name}() carries decorator @{ast.unparse(dec)}, which is "
                        f"not a known-pure memoiser. Stripping it could change results. "
                        f"Review tests/app_loader.py before proceeding."
                    )
            node.decorator_list = []  # safe: memoisation only
        picked.append((src, node))

    # Constants must be defined before the functions that default to them.
    picked.sort(key=lambda pair: 0 if isinstance(pair[1], ast.Assign) else 1)

    # Compiled one node at a time, each against its own filename, so a traceback
    # from inside an extracted function points at the file it really came from.
    for src, node in picked:
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)
        exec(compile(module, filename=str(src), mode="exec"), namespace)

    return {n: namespace[n] for n in (*funcs, *consts)}


def definition_sources(name: str) -> list[str]:
    """Which source files define `name` at the top level, as repo-relative paths.

    Exposed so a test can assert WHERE something lives without duplicating the
    AST walk — see test_chart_breaks.py.
    """
    hits = _definitions({name}, {name})
    return [_rel(src) for src, _ in hits.get(name, [])]


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


def load_pipeline(*, eligible_history_path=None, dte_by_expiry=None) -> dict:
    """Return {name: obj} for the whole Mission Control pipeline.

    Two arguments exist because two module-level dependencies cannot be passed in
    any other way, and both are real defects rather than testing inconveniences:

    `eligible_history_path` — `_ELIGIBLE_HISTORY_PATH` is `Path(
    "eligible_history.json")`, a RELATIVE path resolved against the working
    directory. Left alone, a test calling `_update_eligible_history()` would
    overwrite the real 599 KB registry in the repo root. Pointing it at tmp_path
    is mandatory, not tidiness. (It also means the production registry silently
    depends on where the dashboard was launched from — noted under DEBT-011.)

    `dte_by_expiry` — `_exp_label()` reads a module GLOBAL of this name, even
    though `_build_non_atm_panel` is handed a `dte_by_expiry` PARAMETER and passes
    it nowhere. In production they are the same object so it works; the parameter
    is decorative. Recorded under DEBT-027 with `config.DB_PATH`.

    The returned dict also carries "_st" — the FakeStreamlit whose session_state
    the pipeline reads and writes — so a test can inspect or seed it.
    """
    st = FakeStreamlit()
    namespace: dict = {
        "pd": pd, "np": np, "math": math, "bisect": bisect, "json": json,
        "Path": Path, "iv_engine": iv_engine, "db": db, "config": config,
        "st": st,
        "dte_by_expiry": {} if dte_by_expiry is None else dte_by_expiry,
        "__builtins__": __builtins__,
    }
    loaded = _load_from_app(_PIPELINE_FUNCS, _PIPELINE_CONSTS, namespace,
                            what="Mission Control pipeline")

    if eligible_history_path is not None:
        # Rebind inside the namespace the functions actually close over, not in
        # the returned dict — the readers resolve the name at call time.
        namespace["_ELIGIBLE_HISTORY_PATH"] = Path(eligible_history_path)
        loaded["_ELIGIBLE_HISTORY_PATH"] = namespace["_ELIGIBLE_HISTORY_PATH"]

    loaded["_st"] = st
    loaded["_namespace"] = namespace
    return loaded
