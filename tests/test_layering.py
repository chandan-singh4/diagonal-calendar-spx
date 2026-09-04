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
import re
from pathlib import Path

import pytest
from app_loader import APP_PATH

ROOT = Path(__file__).resolve().parent.parent
CORE_DIR = ROOT / "core"
DATAACCESS_DIR = ROOT / "dataaccess"
STATE_DIR = ROOT / "state"
VIEWS_DIR = ROOT / "views"
SERVICES_DIR = ROOT / "services"
UI_DIR = ROOT / "ui"


def page_sources() -> list[Path]:
    """app.py and everything M2 step 2.5 lifted out of it.

    The source-anchored tests below used to read app.py alone, because app.py
    was where all of this lived. Several of them started failing the moment
    the code moved — each with its own "re-point this test" message, which is
    exactly what those messages are for. They are pointed at the SET rather
    than at a new single file so that the next move does not silently empty
    them: a check that scans a file the code has left passes vacuously, and a
    vacuous pass is indistinguishable from a real one.
    """
    return [APP_PATH, *sorted(SERVICES_DIR.glob("*.py")), *sorted(UI_DIR.glob("*.py"))]

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

# services/ is the page's data layer, added in M2 step 2.5. It is the ONLY
# layer allowed both `config` and `streamlit` — the memoised loaders are
# @st.cache_data and they supply config.DB_PATH, which is precisely the pair
# every other layer is kept away from. What it must not do is draw, or reach
# outward to the broker.
FORBIDDEN_SERVICES = {
    "schwab_client":  "the broker. The dashboard reads history; it never collects",
    "requests":       "network I/O",
    "views":          "a tab. services/ is called BY the page, not the other way round",
    "ui":             "page chrome. Same inversion — data must not depend on chrome",
}

# ui/ DRAWS the page chrome — theme, sidebar, header, controls bar, refresh.
# streamlit is the point here. What a ui/ module must not do is FETCH: same
# rule as views/, and the same invisible failure if broken (a page that looks
# identical while re-querying SQLite on every rerun).
FORBIDDEN_UI = {
    "db":             "the database. A ui/ module is handed its data",
    "sqlite3":        "the database, one layer down",
    "dataaccess":     "the reads directly, BYPASSING the @st.cache_data wrappers",
    "schwab_client":  "the broker. The token age arrives as an argument",
    "requests":       "network I/O",
    "views":          "a tab. Chrome renders around the tabs, not from them",
}

LAYERS = [
    pytest.param(CORE_DIR, FORBIDDEN_CORE, id="core"),
    pytest.param(DATAACCESS_DIR, FORBIDDEN_DATAACCESS, id="dataaccess"),
    pytest.param(STATE_DIR, FORBIDDEN_STATE, id="state"),
    pytest.param(VIEWS_DIR, FORBIDDEN_VIEWS, id="views"),
    pytest.param(SERVICES_DIR, FORBIDDEN_SERVICES, id="services"),
    pytest.param(UI_DIR, FORBIDDEN_UI, id="ui"),
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

    # Widened in step 2.5 from views/ alone to every directory that step
    # moved code INTO. That step took ~2,100 lines out of app.py across six
    # modules, so "the extraction copied instead of moving" had six new ways
    # to happen, not one.
    duplicated = []
    for path in view_modules() + modules_in(SERVICES_DIR) + modules_in(UI_DIR):
        for node in _tree(path).body:
            if isinstance(node, ast.FunctionDef) and node.name in in_app:
                duplicated.append(f"{node.name} — in app.py AND {_rel(path)}")
    assert not duplicated, (
        "these are defined twice; the extraction copied instead of moving:\n  "
        + "\n  ".join(duplicated)
    )


def test_every_consumer_of_a_timestamped_read_converts_it_for_display():
    """DEBT-030's standing guard, and the reason that fix was survivable.

    `load_atm_hist`, `load_atm_hist_fb` and `load_contract_hist` return ZONED
    UTC. Anything that plots one must put it through `to_display_time` first.
    Miss a single site and the chart's x-axis moves four or five hours: every
    session break lands in the wrong place, the day's shape is wrong, and the
    picture still looks completely plausible. No golden test can see it — the
    fixtures would simply be recaptured against the shifted values — and the
    render comparison cannot either, because AppTest cannot read inside a
    chart.

    So it is asserted structurally: every call to one of those loaders must sit
    inside a to_display_time(...) call. Crude, and it is the only check that
    covers this at all.
    """
    LOADERS = {"load_atm_hist", "load_atm_hist_fb", "load_contract_hist"}
    offenders = []

    for path in [*page_sources(), *view_modules()]:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

        # Calls that are DIRECTLY wrapped: to_display_time(loader(...), tz)
        wrapped = {
            id(arg)
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and ((isinstance(n.func, ast.Name) and n.func.id == "to_display_time")
                 or (isinstance(n.func, ast.Attribute) and n.func.attr == "to_display_time"))
            for arg in n.args
        }

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = (node.func.attr if isinstance(node.func, ast.Attribute)
                    else getattr(node.func, "id", None))
            # The leading underscore is stripped before matching, and that is
            # not cosmetic. The memo wrappers are `_load_atm_hist`; the names
            # here were the un-underscored ones, so EVERY call site outside
            # views/ was invisible to this check. Found by mutation during M2
            # step 2.5: unwrapping app.py's ATM history read changed nothing
            # and the test stayed green. views/ was covered only because a
            # view calls `ctx.load_atm_hist_fb(...)`, which happens to carry
            # no underscore.
            if name is None or name.lstrip("_") not in LOADERS or id(node) in wrapped:
                continue
            # The memo wrappers (services/loaders.py since step 2.5) are
            # definitions, not consumers: they call queries.<loader> to BUILD
            # the cached read. Converting there would put the display decision
            # straight back in the data path.
            if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) \
                    and node.func.value.id == "queries":
                continue
            offenders.append(f"{_rel(path)}:{node.lineno} — {name}(...)")

    assert not offenders, (
        "these read timestamps and never convert them for display; if any of "
        "them reaches a chart its x-axis is silently hours out:\n  "
        + "\n  ".join(offenders)
    )


def test_the_extracted_tabs_are_dispatched_from_app():
    """app.py must reference each view's render — an extracted tab that
    nothing invokes is a blank page, and no other test here would notice.

    Asserted on the parsed tree rather than as a substring, because step 2.5
    replaced six literal `view_x.render(VIEW_CTX)` call sites with one
    dispatch table holding the renderers as VALUES. The old check looked for
    the text "x.render(" and would have failed on correct code; matching
    `view_x.render` as an attribute access covers both forms.

    Stronger than the substring version in one way that matters: it compares
    the SETS, so a view that is referenced twice while another is missed —
    the copy-paste failure this file has already seen once — is caught, where
    a per-name `in` check would pass.
    """
    tree = ast.parse(APP_PATH.read_text(encoding="utf-8"), filename=str(APP_PATH))
    dispatched = {
        node.value.id.removeprefix("view_")
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "render"
        and isinstance(node.value, ast.Name) and node.value.id.startswith("view_")
    }
    expected = {path.stem for path in view_modules()}
    assert dispatched == expected, (
        f"app.py dispatches {sorted(dispatched)} but views/ holds "
        f"{sorted(expected)} — a tab was extracted and never wired back in, "
        f"or wired in twice under one name"
    )


def test_layers_import_without_a_dashboard():
    """Each layer must stand up on its own, not only inside a Streamlit run."""
    for path in (all_modules() + modules_in(VIEWS_DIR)
                 + modules_in(SERVICES_DIR) + modules_in(UI_DIR)):
        importlib.import_module(f"{path.parent.name}.{path.stem}")


# ─────────────────────────────────────────────────────────────────────────────
# ui/ — the same two rules views/ has, for the same two reasons
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", modules_in(UI_DIR), ids=_rel)
def test_a_ui_module_never_reaches_for_the_configured_database(path: Path):
    """`config` is allowed in ui/ — ui/sidebar.py reads POLL_INTERVAL_NORMAL
    and POLL_INTERVAL_EVENT, genuine display settings. The two dangerous
    attributes are named instead, exactly as for dataaccess/ and views/.

    STATE_DIR matters as much as DB_PATH here: ui/locks.py writes entry locks
    through services/sidecars.py, and a ui/ module that could resolve the
    state directory itself would re-create DEBT-011 — sidecar files resolved
    against the working directory, silently empty when the app is launched
    from anywhere but the project root.
    """
    hits = [
        node for node in ast.walk(_tree(path))
        if isinstance(node, ast.Attribute) and node.attr in {"DB_PATH", "STATE_DIR"}
        and isinstance(node.value, ast.Name) and node.value.id == "config"
    ]
    assert not hits, (
        f"{_rel(path)} reads config.{hits[0].attr}. Chrome is handed its data "
        f"and its files; knowing where they live is how it starts fetching them."
    )


@pytest.mark.parametrize("path", modules_in(UI_DIR), ids=_rel)
def test_a_ui_module_draws_nothing_at_import_time(path: Path):
    """Module level holds imports and definitions only — the views/ rule.

    A bare `st.something(...)` at module scope would draw ONCE, into whichever
    render happened to trigger the import, and never again: Streamlit reruns
    the script, not the imports. This is a live hazard for ui/ specifically,
    because the code it holds was procedural top-level statements in app.py
    until step 2.5 and wrapping every one of them in a function was the move.
    """
    offenders = [
        f"line {n.lineno}: {ast.unparse(n)[:70]}"
        for n in _tree(path).body
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
    ]
    assert not offenders, (
        f"{_rel(path)} executes a call at module level:\n  "
        + "\n  ".join(offenders)
        + "\nImports are re-used across reruns; only the functions re-run."
    )


def test_the_reauth_command_points_at_the_project_root():
    """The one thing in step 2.5 the render comparison could NOT have checked.

    `reauth_command` builds `cd "<project dir>"` from `Path(__file__)`. In
    app.py that was `.parent`; in ui/sidebar.py the same expression means
    ui/, so it had to become `.parent.parent`. Get it wrong and the command
    silently tells you to cd into a directory with no scripts/reauth.py —
    and you only find out when the token has already expired and the
    collector has been blind for hours.

    The before/after render harness is blind to this by construction: it
    REDACTS the checkout path as known noise, because a git worktree and the
    working tree must legitimately differ there. So it is asserted directly.
    """
    from ui.sidebar import PROJECT_DIR, reauth_command

    assert (PROJECT_DIR / "app.py").is_file(), (
        f"ui.sidebar.PROJECT_DIR is {PROJECT_DIR}, which contains no app.py — "
        f"the re-auth command would tell you to cd somewhere wrong"
    )
    assert (PROJECT_DIR / "scripts" / "reauth.py").is_file(), (
        f"{PROJECT_DIR}/scripts/reauth.py does not exist — the command the "
        f"dashboard hands you would fail when pasted"
    )
    assert str(PROJECT_DIR) in reauth_command("python.exe")


def test_the_stylesheet_exists_and_is_wrapped_in_a_style_tag():
    """A missing or empty theme.css renders an UNSTYLED page, not an error.

    The 757-line stylesheet became a file in step 2.5. Nothing else in the
    suite would notice if it vanished, was truncated, or stopped being
    wrapped in <style> — every test would pass and the dashboard would come
    up as unformatted black-on-white HTML.
    """
    from ui import theme

    assert theme.STYLESHEET.is_file(), f"{theme.STYLESHEET} is missing"
    css = theme.css()
    assert len(css.splitlines()) > 500, (
        f"assets/theme.css is only {len(css.splitlines())} lines — it was 754 "
        f"when extracted; this looks truncated"
    )
    assert "<style" not in css, "the .css file should hold CSS, not HTML tags"
    assert ":root {" in css, "the design tokens block is missing"


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
    """Every call to `func_name` across app.py and the modules split out of it."""
    calls: list[ast.Call] = []
    for path in page_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        calls.extend(
            n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and (
                (isinstance(n.func, ast.Name) and n.func.id == func_name)
                or (isinstance(n.func, ast.Attribute) and n.func.attr == func_name)
            )
        )
    return calls


def test_the_offset_sweep_is_handed_the_memoised_scanner():
    calls = _calls_to("scan_all_offsets")
    assert calls, "app.py no longer calls scan_all_offsets — re-point this test"
    for call in calls:
        assert any(kw.arg == "compute" for kw in call.keywords), (
            "scan_all_offsets() called without compute= — Phase A would fall "
            "back to the UNCACHED scanner and recompute 21 offsets on every "
            "rerun. Pass the memoised wrapper: compute=compute_transform_scanner"
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


def test_the_refresh_poller_adopts_the_snapshot_before_it_reruns():
    """BUG-020. The freeze that made the dashboard unusable.

    The live-refresh fragment reruns the app when a newer snapshot appears.
    `st.rerun()` ABORTS the current script run where it stands — and the line
    that records which snapshot is being shown sits ~100 lines further down,
    so it never executed. The next run called the poller again, found the same
    stale value, and rerun again: an infinite loop at 100% CPU that never
    reached the point where anything is drawn. The page froze, charts stopped
    responding to the controls, and clicks did nothing.

    It needs one new snapshot to trigger, so the first page load always looks
    fine — which is why this survived so long and read as "sometimes frozen".

    Asserted on the source because there is no seam to call: no test can run
    the fragment (AppTest does not fire fragment timers at all, which is
    exactly why the suite never saw this).

    Re-pointed at ui/refresh.py in M2 step 2.5, where the poller now lives.
    It failed on its own anchor during that move rather than passing
    vacuously against a file the code had left — which is the only reason
    this re-point is a re-point and not a silent loss of the guard.
    """
    src = (ROOT / "ui" / "refresh.py").read_text(encoding="utf-8")
    start = src.index("def _live_refresh_poller()")
    # The CALL site, not the def line — "_live_refresh_poller()" also matches
    # inside "def _live_refresh_poller()", which silently sliced this down to
    # the four characters "def " on the first attempt and made the assertion
    # below fail for entirely the wrong reason.
    call_site = re.search(r"\n\s*_live_refresh_poller\(\)\s*\n", src[start:])
    assert call_site, "the poller is defined but never invoked"
    block = src[start:start + call_site.start()]

    adopt = 'st.session_state["_active_snapshot_id"] = _latest_id'
    assert block.count(adopt) == 2, (
        "the poller should record the snapshot in BOTH branches — the "
        "first-run branch and the newer-snapshot branch"
    )
    # Match the STATEMENT — a line whose only content is the code — not the
    # bare text, which also occurs in the comment explaining this very bug.
    # The first version of this test matched the prose and failed against
    # correct code. Indentation is matched as "whatever leads the line" rather
    # than a fixed depth: step 2.5 moved this function one level deeper inside
    # `install()`, and a hardcoded depth would have failed for the wrong
    # reason a second time.
    rerun_at = [m.start() for m in re.finditer(r"\n[ \t]*st\.rerun\(\)", block)][-1]
    adopt_at = [m.start() for m in
                re.finditer(r"\n[ \t]*" + re.escape(adopt), block)][-1]
    assert adopt_at < rerun_at, (
        "st.rerun() is called BEFORE the poller records the snapshot it is "
        "rerunning for. rerun() aborts the script, so the record never "
        "happens, and the next run reruns for the same reason — forever. "
        "That is BUG-020, and it froze the whole dashboard."
    )


def test_the_gamma_exposure_controls_never_ask_for_a_second_script_run():
    """A chevron press must not cost two passes over app.py.

    A control that records its new value and calls st.rerun() so the page
    catches up doubles the cost of every click —
    measured at 3.8s a pass on this page, so ~7.6s of nothing happening — and
    the second pass exists only to show a string the first pass could have
    shown had it read the buttons before drawing the value.

    Pinned as a static check rather than a timing test: a timing test on a
    Streamlit page is slow, flaky, and would not say WHICH change made it
    slow. The failure this guards is a specific line coming back.
    """
    source = (VIEWS_DIR / "gex.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="views/gex.py")
    reruns = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute) and node.func.attr == "rerun"
    ]
    assert not reruns, (
        f"views/gex.py calls st.rerun() at line(s) "
        f"{[n.lineno for n in reruns]} — every one doubles the cost of the "
        f"click that triggers it. Read the widget before drawing what it "
        f"controls instead."
    )


def test_every_memoised_gex_figure_takes_the_expiry_in_its_cache_key():
    """The bug this pins was invisible: the panel simply did not change.

    views/gex.py passes its DataFrames to @st.cache_data with a leading
    underscore so Streamlit skips hashing them — hashing a 3,000-row chain
    costs more than the redraw it saves. The cost is that the frame is then
    NOT part of the cache key, so a helper that took only (spot, snapshot_id)
    returned the first table it ever computed no matter which expiry was
    selected. The positioning panel showed one expiry's numbers under every
    other expiry's label for as long as the snapshot lasted.

    So: any cached helper here that receives an underscored frame must also
    take an `expiry` argument, because expiry is what filtered that frame.
    The exemptions are named individually below, each with the reason its
    frame does not depend on the Expiry control — an exemption list is
    checkable, "it looked fine" is not.
    """
    # Read straight from the database at a fixed scope, never from the
    # expiry-filtered frame:
    #   _flow_figure   — 0DTE only, by definition (dte_max=0)
    #   _bubble_points — the whole chain, deliberately: the panel exists to
    #   _bubble_figure   COMPARE expiries, and its caption says the control
    #                    does not apply to it
    EXEMPT = {"_flow_figure", "_bubble_points", "_bubble_figure"}
    import ast
    import inspect

    from views import gex as view_gex

    tree = ast.parse(inspect.getsource(view_gex))
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        cached = any("cache_data" in ast.dump(d) for d in node.decorator_list)
        if not cached:
            continue
        args = [a.arg for a in node.args.args]
        takes_frame = any(a.startswith("_") for a in args)
        if node.name in EXEMPT:
            continue
        if takes_frame and "expiry" not in args and "scope" not in args:
            offenders.append(f"{node.name}({', '.join(args)})")

    assert not offenders, (
        "cached on an unhashed frame without the filter that produced it: "
        + "; ".join(offenders))
