"""
The layer rules, enforced. Renamed from test_core_layering.py in M2 step 2.2,
when a second layer appeared; `state/` and `views/` will land here too.

WHY THIS EXISTS. The M2 extraction is only worth doing if the layers stay
separated afterwards, and layers do not drift in one big obvious commit — they
drift one convenient import at a time. `import db` inside a core module would
never fail a golden test: the function would keep returning the right numbers,
while quietly becoming impossible to check without a database.

WHAT IT CANNOT DO. It reads import statements and call signatures, so a module
could still cheat via importlib or by having something injected. That is fine —
this is a guard against drift, not against a determined author.
tests/app_loader.py attacks the same rule from the other side by executing these
functions in a namespace that contains no page at all, where a stray dependency
raises NameError.
"""
from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest
from app_loader import APP_PATH

ROOT = Path(__file__).resolve().parent.parent
CORE_DIR = ROOT / "core"
DATAACCESS_DIR = ROOT / "dataaccess"
STATE_DIR = ROOT / "state"
VIEWS_DIR = ROOT / "views"

# core/ computes. It is handed data and returns data.
FORBIDDEN_CORE = {
    "streamlit":      "the page. core/ computes; it does not render, cache, or read widgets",
    "db":             "the database. core/ takes data as arguments",
    "config":         "carries DB_PATH and app settings — a hidden global input",
    "sqlite3":        "the database, one layer down",
    "schwab_client":  "the broker. Network I/O belongs nowhere near pure calculation",
    "requests":       "network I/O",
}

# dataaccess/ reads the database — that IS its job, so db and config are fine
# here. What it must not do is render, or decide for itself which database.
FORBIDDEN_DATAACCESS = {
    "streamlit":      "the page. dataaccess/ reads; the memo and the widgets stay in app.py",
    "schwab_client":  "the broker. Collecting is the collector's job, not the dashboard's",
    "requests":       "network I/O",
}

# state/ persists the JSON sidecar files. It is told everything: which
# directory, which timezone. It reads no configuration of its own.
FORBIDDEN_STATE = {
    "streamlit":      "the page. state/ persists; it does not render",
    "config":         "state/ is HANDED its directory and timezone — that is the DEBT-011 fix",
    "db":             "the database. These are JSON sidecar files, not market data",
    "sqlite3":        "the database, one layer down",
    "schwab_client":  "the broker",
    "requests":       "network I/O",
}

# views/ DRAWS — so streamlit is not merely allowed here, it is the point.
# What a view must not do is fetch its own data. The memoised @st.cache_data
# wrappers live in app.py and arrive on the ViewContext; a view that imported
# `dataaccess` would render exactly the same page while re-querying SQLite on
# every rerun — no wrong number, no error, just a dashboard that gets slower
# and a cache that looks like it is working.
FORBIDDEN_VIEWS = {
    "db":             "the database. A view is handed data; it does not fetch it",
    "sqlite3":        "the database, one layer down",
    "dataaccess":     "the reads directly, BYPASSING app.py's @st.cache_data wrappers",
    "schwab_client":  "the broker. Nothing on a page should talk to Schwab",
    "requests":       "network I/O",
}

LAYERS = [
    pytest.param(CORE_DIR, FORBIDDEN_CORE, id="core"),
    pytest.param(DATAACCESS_DIR, FORBIDDEN_DATAACCESS, id="dataaccess"),
    pytest.param(STATE_DIR, FORBIDDEN_STATE, id="state"),
    pytest.param(VIEWS_DIR, FORBIDDEN_VIEWS, id="views"),
]


def modules_in(directory: Path) -> list[Path]:
    return sorted(p for p in directory.glob("*.py") if p.name != "__init__.py")


def all_modules() -> list[Path]:
    """The layers that must stay free of the page. views/ IS the page, so it
    is deliberately absent — see view_modules() below for its own rules."""
    return modules_in(CORE_DIR) + modules_in(DATAACCESS_DIR) + modules_in(STATE_DIR)


def view_modules() -> list[Path]:
    return [p for p in modules_in(VIEWS_DIR) if p.name != "context.py"]


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imported_roots(path: Path) -> set[str]:
    """Top-level module names imported by `path`, however they were written."""
    roots: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


# ─────────────────────────────────────────────────────────────────────────────
# The import rules
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("directory", "forbidden"), LAYERS)
def test_the_layer_has_modules_to_check(directory: Path, forbidden: dict):
    """A glob that silently matches nothing would make every test below pass."""
    assert modules_in(directory), f"no modules found in {directory}"


@pytest.mark.parametrize(("directory", "forbidden"), LAYERS)
def test_layer_imports_nothing_forbidden(directory: Path, forbidden: dict):
    problems = []
    for path in modules_in(directory):
        for name in sorted(_imported_roots(path) & forbidden.keys()):
            problems.append(f"{_rel(path)} imports {name!r} — {forbidden[name]}")
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("path", all_modules(), ids=_rel)
def test_module_does_not_reach_for_streamlit(path: Path):
    """Belt and braces for the import check: catches a bare `st` global that
    was never imported. That would raise NameError on its own, but not if the
    module is ever exec'd inside app.py's namespace — which is exactly what
    tests/app_loader.py does, so the mistake could hide there.

    Matched on the parsed tree, not the text: `st.` as a substring also occurs
    in any prose sentence ending in a word like "list."
    """
    used = {n.id for n in ast.walk(_tree(path)) if isinstance(n, ast.Name)}
    assert "st" not in used, f"{_rel(path)} uses the name `st` — the page belongs to app.py"


# ─────────────────────────────────────────────────────────────────────────────
# dataaccess/ — the database location must be given, never assumed
#
# This is the whole point of the layer, not a detail. Before M2 step 2.2 these
# reads took the path from config.DB_PATH, so nothing could aim them anywhere
# else and every test had to overwrite that global — a test modifying the thing
# it is testing (DEBT-027).
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", modules_in(DATAACCESS_DIR), ids=_rel)
def test_dataaccess_never_reaches_for_the_configured_database(path: Path):
    hits = [
        node for node in ast.walk(_tree(path))
        if isinstance(node, ast.Attribute) and node.attr == "DB_PATH"
        and isinstance(node.value, ast.Name) and node.value.id == "config"
    ]
    assert not hits, (
        f"{_rel(path)} references config.DB_PATH. The database location must "
        f"arrive as an argument, or nothing can point these reads at a test "
        f"database without overwriting a global (DEBT-027)."
    )


@pytest.mark.parametrize("path", modules_in(DATAACCESS_DIR), ids=_rel)
def test_every_dataaccess_function_takes_db_path_first(path: Path):
    offenders = []
    for node in _tree(path).body:
        if not isinstance(node, ast.FunctionDef) or node.name.startswith("_"):
            continue
        args = [a.arg for a in node.args.args]
        if not args or args[0] != "db_path":
            offenders.append(f"{_rel(path)}::{node.name}({', '.join(args) or ''})")
    assert not offenders, (
        "these take the database location somewhere other than their first "
        "argument, or not at all:\n  " + "\n  ".join(offenders)
    )


# ─────────────────────────────────────────────────────────────────────────────
# state/ — a filename with no directory is the DEBT-011 defect itself
#
# `Path("eligible_history.json")` resolves against the working directory. Launch
# the dashboard from anywhere but the project root and it finds no registry,
# creates an empty one there, and shows a panel that has forgotten everything —
# with no error. Every path in this layer must be built from the state_dir it
# was handed.
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", modules_in(STATE_DIR), ids=_rel)
def test_state_never_builds_a_path_from_a_bare_filename(path: Path):
    offenders = [
        ast.unparse(node) for node in ast.walk(_tree(path))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "Path" and node.args
        and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)
    ]
    assert not offenders, (
        f"{_rel(path)} builds a path from a literal string: {offenders}. That "
        f"resolves against the working directory, which is DEBT-011 exactly. "
        f"Build it from the state_dir the caller supplied."
    )


# ─────────────────────────────────────────────────────────────────────────────
# views/ — one entry point, and everything it needs arrives through it
#
# The defect being designed out is the one step 2.4 exists to remove: a tab
# body that reads a name app.py's prelude happened to leave lying in the
# module namespace. Inside a function that fails loudly as a NameError, so it
# cannot survive — but only while `render(ctx)` stays the sole way in. Add a
# second parameter with a default, or a module-level constant computed from a
# query, and the back door is open again.
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", view_modules(), ids=_rel)
def test_every_view_exposes_render_taking_only_the_context(path: Path):
    renders = [
        n for n in _tree(path).body
        if isinstance(n, ast.FunctionDef) and n.name == "render"
    ]
    assert len(renders) == 1, f"{_rel(path)} must define exactly one render()"
    node = renders[0]
    args = [a.arg for a in node.args.args]
    assert args == ["ctx"], (
        f"{_rel(path)}::render({', '.join(args)}) — a view takes the "
        f"ViewContext and nothing else. An extra parameter is a dependency "
        f"that no longer has to appear on the context, which is how the "
        f"prelude's globals grew in the first place."
    )
    assert not node.args.defaults and not node.args.kwonlyargs, (
        f"{_rel(path)}::render has default or keyword-only arguments — a "
        f"default is an input the caller never has to think about"
    )


@pytest.mark.parametrize("path", view_modules(), ids=_rel)
def test_a_view_draws_nothing_at_import_time(path: Path):
    """Module level must hold imports and definitions only.

    A bare `st.something(...)` at module scope would draw on import — once,
    into whichever tab happened to be rendering — and never again, because
    Python caches the module. Streamlit reruns the script, not the imports.
    """
    offenders = [
        f"line {n.lineno}: {ast.unparse(n)[:70]}"
        for n in _tree(path).body
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
    ]
    assert not offenders, (
        f"{_rel(path)} executes a call at module level:\n  "
        + "\n  ".join(offenders)
        + "\nImports are re-used across reruns; only render() re-runs."
    )


@pytest.mark.parametrize("path", view_modules(), ids=_rel)
def test_a_view_never_reaches_for_the_configured_database(path: Path):
    """`config` is allowed in views/, but only just.

    views/edge.py imports it for one line — `config.DISPLAY_TIMEZONE`, a
    genuine display setting. Rebinding it through the context would have
    meant editing the moved body, and not editing the body is the whole
    basis of step 2.4. So the import stays and the dangerous attribute is
    named instead: the same rule dataaccess/ has, for the same reason. A
    view that can find the database is a view that can query it, and the
    memo it would bypass is invisible when it goes missing.
    """
    hits = [
        node for node in ast.walk(_tree(path))
        if isinstance(node, ast.Attribute) and node.attr in {"DB_PATH", "STATE_DIR"}
        and isinstance(node.value, ast.Name) and node.value.id == "config"
    ]
    assert not hits, (
        f"{_rel(path)} reads config.{hits[0].attr}. A view is handed its data "
        f"and its files; knowing where they live is how it starts fetching "
        f"them itself."
    )


def test_no_view_helper_is_also_left_behind_in_app():
    """The failure mode is a COPY, not a move.

    Found for real during step 2.4: `_render_note` was moved into
    views/edge.py and the original stayed in app.py, called by nothing. Two
    definitions of one function is the worst of both — the page runs one,
    anything reading app.py's source measures the other, and neither is
    wrong enough to raise. It is the same hazard
    test_break_sessions_is_defined_once_where_the_loader_expects_it guards
    for core/, generalised to every view.
    """
    app_tree = ast.parse(APP_PATH.read_text(encoding="utf-8"), filename=str(APP_PATH))
    in_app = {n.name for n in app_tree.body if isinstance(n, ast.FunctionDef)}

    duplicated = []
    for path in view_modules():
        for node in _tree(path).body:
            if isinstance(node, ast.FunctionDef) and node.name in in_app:
                duplicated.append(f"{node.name} — in app.py AND {_rel(path)}")
    assert not duplicated, (
        "these are defined twice; the extraction copied instead of moving:\n  "
        + "\n  ".join(duplicated)
    )


def test_the_extracted_tabs_are_dispatched_from_app():
    """app.py must actually call each view — an extracted tab that nothing
    invokes is a blank page, and no other test here would notice."""
    source = APP_PATH.read_text(encoding="utf-8")
    for path in view_modules():
        assert f"{path.stem}.render(" in source.replace("view_", ""), (
            f"nothing in app.py calls {path.stem}'s render() — the tab was "
            f"extracted but never wired back in"
        )


def test_layers_import_without_a_dashboard():
    """Each layer must stand up on its own, not only inside a Streamlit run."""
    for path in all_modules() + modules_in(VIEWS_DIR):
        importlib.import_module(f"{path.parent.name}.{path.stem}")


# ─────────────────────────────────────────────────────────────────────────────
# The seams the extraction created — and the cost of getting them wrong
#
# Purity has a price. Both layers hand a memoised callable back down into a
# pure function, because core/ and dataaccess/ cannot apply @st.cache_data
# themselves. Drop the argument and everything still works, still returns
# identical numbers, and every other test still passes — while the dashboard
# quietly does the expensive thing again on every rerun.
#
# The first of these exists because that fault SURVIVED the step 2.1 mutation
# run (ADR-032). Asserted against the source, because a missing performance
# seam has no observable behaviour to assert on.
# ─────────────────────────────────────────────────────────────────────────────

def _calls_to(func_name: str) -> list[ast.Call]:
    tree = ast.parse(APP_PATH.read_text(encoding="utf-8"), filename=str(APP_PATH))
    return [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and (
            (isinstance(n.func, ast.Name) and n.func.id == func_name)
            or (isinstance(n.func, ast.Attribute) and n.func.attr == func_name)
        )
    ]


def test_the_offset_sweep_is_handed_the_memoised_scanner():
    calls = _calls_to("_scan_all_offsets")
    assert calls, "app.py no longer calls _scan_all_offsets — re-point this test"
    for call in calls:
        assert any(kw.arg == "compute" for kw in call.keywords), (
            "_scan_all_offsets() called without compute= — Phase A would fall "
            "back to the UNCACHED scanner and recompute 21 offsets on every "
            "rerun. Pass the memoised wrapper: compute=_compute_transform_scanner"
        )


def test_the_atm_history_fallback_is_handed_the_memoised_loader():
    calls = _calls_to("load_atm_hist_fb")
    assert calls, "app.py no longer calls queries.load_atm_hist_fb — re-point this test"
    for call in calls:
        assert any(kw.arg == "load" for kw in call.keywords), (
            "load_atm_hist_fb() called without load= — the fallback's second "
            "read would go to the database instead of reusing the memoised "
            "result. Pass load=_load_atm_hist."
        )
