"""
core/ must stay pure. This is the rule, enforced.

WHY THIS EXISTS. The M2 extraction is only worth doing if the layers stay
separated afterwards, and layers do not drift in one big obvious commit — they
drift one convenient import at a time. `import db` inside a core module would
never fail a golden test: the function would keep returning the right numbers,
while quietly becoming impossible to check without a database.

WHAT IT CANNOT DO. It reads import statements, so a module could still cheat
via importlib or by having something injected. That is fine — this is a guard
against drift, not against a determined author. tests/app_loader.py attacks the
same rule from the other side by executing these functions in a namespace that
contains no database and no page at all, where a stray dependency raises
NameError.
"""
from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest
from app_loader import APP_PATH

CORE_DIR = Path(__file__).resolve().parent.parent / "core"

# module -> why core/ may not have it
FORBIDDEN = {
    "streamlit":      "the page. core/ computes; it does not render, cache, or read widgets",
    "db":             "the database. core/ takes data as arguments",
    "config":         "carries DB_PATH and app settings — a hidden global input",
    "sqlite3":        "the database, one layer down",
    "schwab_client":  "the broker. Network I/O belongs nowhere near pure calculation",
    "requests":       "network I/O",
}


def core_modules() -> list[Path]:
    return sorted(p for p in CORE_DIR.glob("*.py") if p.name != "__init__.py")


def _imported_roots(path: Path) -> set[str]:
    """Top-level module names imported by `path`, however they were written."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_there_are_core_modules_to_check():
    """A glob that silently matches nothing would make every test below pass."""
    assert core_modules(), f"no modules found in {CORE_DIR}"


@pytest.mark.parametrize("path", core_modules(), ids=lambda p: p.name)
def test_core_module_imports_nothing_forbidden(path: Path):
    offenders = _imported_roots(path) & FORBIDDEN.keys()
    assert not offenders, "\n".join(
        f"core/{path.name} imports {name!r} — {FORBIDDEN[name]}"
        for name in sorted(offenders)
    )


@pytest.mark.parametrize("path", core_modules(), ids=lambda p: p.name)
def test_core_module_does_not_reach_for_streamlit(path: Path):
    """Belt and braces for the import check: catches a bare `st` global that
    was never imported. That would raise NameError on its own, but not if the
    module is ever exec'd inside app.py's namespace — which is exactly what
    tests/app_loader.py does, so the mistake could hide there.

    Matched on the parsed tree, not the text: `st.` as a substring also occurs
    in any prose sentence ending in a word like "list."
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "st" not in used, (
        f"core/{path.name} uses the name `st` — {FORBIDDEN['streamlit']}"
    )


def test_core_package_imports_without_a_dashboard():
    """The layer must stand up on its own, not only inside a Streamlit run."""
    for path in core_modules():
        importlib.import_module(f"core.{path.stem}")


# ─────────────────────────────────────────────────────────────────────────────
# The seam the extraction created — and the cost of getting it wrong
#
# Purity has a price here. `_compute_transform_scanner` carries a Streamlit memo
# that core/ cannot apply itself, so app.py wraps it and passes the wrapper into
# `_scan_all_offsets(compute=...)`. Drop that one keyword and everything still
# works, still returns identical numbers, and every one of the other 559 tests
# still passes — while Phase A quietly recomputes all 21 offsets on every rerun
# instead of reusing saved results shared with the Scanner tab.
#
# This test exists because that fault SURVIVED the M2 mutation run (ADR-032).
# Same shape as the BUG-002 wiring guard in test_chart_breaks.py: asserted
# against the source, because a missing performance seam has no observable
# behaviour to assert on.
# ─────────────────────────────────────────────────────────────────────────────

def test_the_offset_sweep_is_handed_the_memoised_scanner():
    tree = ast.parse(APP_PATH.read_text(encoding="utf-8"), filename=str(APP_PATH))
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        and n.func.id == "_scan_all_offsets"
    ]
    assert calls, "app.py no longer calls _scan_all_offsets — re-point this test"
    for call in calls:
        assert any(kw.arg == "compute" for kw in call.keywords), (
            "_scan_all_offsets() called without compute= — Phase A would fall "
            "back to the UNCACHED scanner and recompute 21 offsets on every "
            "rerun. Pass the memoised wrapper: compute=_compute_transform_scanner"
        )
