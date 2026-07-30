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

LAYERS = [
    pytest.param(CORE_DIR, FORBIDDEN_CORE, id="core"),
    pytest.param(DATAACCESS_DIR, FORBIDDEN_DATAACCESS, id="dataaccess"),
]


def modules_in(directory: Path) -> list[Path]:
    return sorted(p for p in directory.glob("*.py") if p.name != "__init__.py")


def all_modules() -> list[Path]:
    return modules_in(CORE_DIR) + modules_in(DATAACCESS_DIR)


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


def test_layers_import_without_a_dashboard():
    """Each layer must stand up on its own, not only inside a Streamlit run."""
    for path in all_modules():
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
