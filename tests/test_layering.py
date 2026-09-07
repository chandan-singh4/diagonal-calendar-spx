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
API_DIR = ROOT / "api"


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

# api/ SERVES — added in M4.1. It is the server's binding layer: the analogue of
# services/ on the page side, and allowed the same `config.DB_PATH` for the same
# reason. What makes it a separate layer rather than a reuse of services/ is the
# clause below.
#
# `services` IS THE ONE THAT MATTERS. Every module in services/ imports
# streamlit, so an `import services` here drags the page into a process that has
# no browser session — and it fails QUIETLY: st.cache_data outside a script run
# degrades rather than raising, so the server would answer correctly while
# re-querying SQLite on every single request. That is the same invisible failure
# the views/ rule guards against, one layer over.
FORBIDDEN_API = {
    "streamlit":      "the page. api/ serves data; a server with a browser session is not a server",
    "services":       "the page's data layer — every module in it imports streamlit",
    "views":          "a tab. Nothing served over HTTP should depend on how a tab draws",
    "ui":             "page chrome",
    "app":            "the dashboard itself",
    "schwab_client":  "the broker. The API serves the record; collecting is the collector's job",
}

LAYERS = [
    pytest.param(CORE_DIR, FORBIDDEN_CORE, id="core"),
    pytest.param(DATAACCESS_DIR, FORBIDDEN_DATAACCESS, id="dataaccess"),
    pytest.param(STATE_DIR, FORBIDDEN_STATE, id="state"),
    pytest.param(VIEWS_DIR, FORBIDDEN_VIEWS, id="views"),
    pytest.param(SERVICES_DIR, FORBIDDEN_SERVICES, id="services"),
    pytest.param(UI_DIR, FORBIDDEN_UI, id="ui"),
    pytest.param(API_DIR, FORBIDDEN_API, id="api"),
]


def modules_in(directory: Path) -> list[Path]:
    return sorted(p for p in directory.glob("*.py") if p.name != "__init__.py")


def all_modules() -> list[Path]:
    """The layers that must stay free of the page. views/ IS the page, so it
    is deliberately absent — see view_modules() below for its own rules."""
    return (modules_in(CORE_DIR) + modules_in(DATAACCESS_DIR)
            + modules_in(STATE_DIR) + modules_in(API_DIR))


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
        #
        # `merge_atm_pair` counts as a converter too, and that is an EARNED
        # exemption, not a hole: it converts both series itself, and
        # test_merge_atm_pair_converts_for_display below fails if it ever
        # stops. Widening this set without that second test would have made
        # the guard say "converted" where it only means "handed to something".
        CONVERTERS = {"to_display_time", "merge_atm_pair", "merge_iv_pair"}
        wrapped = {
            id(arg)
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and ((isinstance(n.func, ast.Name) and n.func.id in CONVERTERS)
                 or (isinstance(n.func, ast.Attribute) and n.func.attr in CONVERTERS))
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


def test_merge_atm_pair_converts_for_display():
    """The other half of the guard above.

    `merge_atm_pair` is accepted there as a converter, so this asserts it
    really is one. Handed zoned UTC, it must return a naive local wall-clock:
    13:30 UTC is 09:30 in New York, and the whole point of DEBT-030 is that
    getting this wrong shifts every session break by hours while the chart
    goes on looking entirely plausible.
    """
    import pandas as pd

    from core.series import merge_atm_pair

    ts = pd.to_datetime(["2026-09-04T13:30:00Z"])
    front = pd.DataFrame({"timestamp": ts, "atm_iv": [20.0]})
    back = pd.DataFrame({"timestamp": ts, "atm_iv": [10.0]})

    out = merge_atm_pair(front, back, "America/New_York")

    assert out["timestamp"].dt.tz is None, "a naive wall-clock is required"
    assert out["timestamp"].iloc[0] == pd.Timestamp("2026-09-04 09:30:00")


def test_merge_iv_pair_converts_for_display_with_the_breaks_off():
    """The other half of the exemption above.

    `views/historical.py` reads two timestamped frames and hands them
    straight to `merge_iv_pair(..., insert_breaks=False)`, so the AST check
    sees no converter at the call site. The panel takes a min, a max and a
    percentile from the RESULT OF A MERGE ON THAT COLUMN -- if one side were
    converted and the other were not, the join would find no shared
    timestamps at all and every window would silently read "No data".

    Proved by breaking: make `merge_iv_pair` skip `to_display_time` and this
    fails.
    """
    import pandas as pd

    from core.series import merge_iv_pair

    ts = pd.to_datetime(["2026-09-04T13:30:00Z"])
    front = pd.DataFrame({"timestamp": ts, "atm_iv": [20.0]})
    back = pd.DataFrame({"timestamp": ts, "atm_iv": [10.0]})

    out = merge_iv_pair(front, back, "America/New_York", insert_breaks=False)

    assert out["timestamp"].dt.tz is None, "a naive wall-clock is required"
    assert out["timestamp"].iloc[0] == pd.Timestamp("2026-09-04 09:30:00")
    assert out["iv_ratio"].iloc[0] == 2.0
    # The breaks really are off: one shared timestamp, one row.
    assert len(out) == 1


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


def test_the_positioning_cache_retires_when_the_frame_changes_shape():
    """A stale cache served the user a KeyError from correct code.

    st.cache_data hashes the body of the function it decorates — not the
    modules that function calls. core/dealer.py gained columns; views/gex.py's
    cached wrapper did not change; a server already running kept handing the
    old frame to the new renderer, which asked for a column that had not
    existed when the entry was built.

    Passing the column tuple makes the shape part of the key, so entries built
    before a change cannot be served after it.
    """
    import inspect

    from core import dealer
    from views import gex as view_gex

    assert "columns" in inspect.signature(view_gex._positioning).parameters

    source = inspect.getsource(view_gex)
    assert "dealer.VERDICT_COLUMNS)" in source, (
        "the columns argument must be the real column tuple — a literal "
        "copied here would stop tracking the frame it is supposed to describe")
    assert isinstance(dealer.VERDICT_COLUMNS, tuple), "must be hashable"


# ── DEBT-031: one threshold, not a literal per screen ───────────────────────

def test_the_calendar_edge_page_never_writes_the_threshold_as_a_literal():
    """THE FAILURE THIS PREVENTS IS SILENT AND VISIBLE AT ONCE.

    `views/edge.py` held FIVE copies of 5.0: the shading test, the badge that
    names it, the caption that explains it, the progress bar's denominator and
    the ETA's target. `core.scanner.TSCAN_THRESHOLD` is 5.0 today, so every
    copy agreed and nothing looked wrong. The moment the threshold moved, the
    chart would have shaded one number while the badge above it named another
    and the progress bar filled toward a third -- on the same screen, at the
    same time.

    The React chart never had this problem: it is served the value and the
    project's rule forbids it a copy. This check holds the old screen to the
    same standard while both are live.

    SCANNING THE SOURCE, not the behaviour, is deliberate. A behavioural test
    would need the threshold to actually change to notice, which is exactly
    the moment it is too late.
    """
    import re

    source = (ROOT / "views" / "edge.py").read_text(encoding="utf-8")
    body = " ".join(line for line in source.splitlines()
                    if not line.lstrip().startswith("#"))

    offenders = re.findall(r"(?<![\w.])5\.0(?![\w])|\$5\.00", body)
    assert not offenders, (
        f"{len(offenders)} literal copies of the transform threshold in "
        "views/edge.py -- import TSCAN_THRESHOLD from core.scanner instead")


# ─────────────────────────────────────────────────────────────────────────────
# The Calendar Edge time axis — three charts, one frame
# ─────────────────────────────────────────────────────────────────────────────

_TIME_AXIS_CHARTS = ("GapChart.tsx", "IvChart.tsx", "IvDualAxis.tsx")


def test_the_list_of_time_charts_has_not_gone_stale():
    """A hand-written list is a list that stops being true.

    Any chart drawn on the session clock takes `sessionAxisRange` -- that is
    what makes it one of these -- so the roster can be recovered from the
    source rather than remembered. `IvScatter.tsx` is deliberately absent: its
    x-axis is an IV ratio, not a time, so it keeps its own frame and should.
    """
    edge = ROOT / "web" / "src" / "edge"
    def is_a_time_chart(path) -> bool:
        text = path.read_text(encoding="utf-8")
        # It draws (imports Plotly) AND it is handed the session window.
        # EdgeTab.tsx names `sessionAxisRange` too, but only to pass it on --
        # it owns no frame, so it is not one of these.
        return "sessionAxisRange" in text and "from 'plotly.js-dist-min'" in text

    time_charts = {path.name for path in edge.glob("*.tsx") if is_a_time_chart(path)}
    assert time_charts == set(_TIME_AXIS_CHARTS), (
        "a chart on the session clock is not covered by the shared-frame "
        f"check: {sorted(time_charts.symmetric_difference(_TIME_AXIS_CHARTS))}"
    )


def test_every_calendar_edge_time_chart_is_drawn_in_the_same_frame():
    """THE FAULT THIS PREVENTS CAME BACK FOUR TIMES.

    Three charts on Calendar Edge share the session clock and are read by
    scanning down the page. They already drew the identical
    `session_axis_range` -- and still did not line up, because Plotly maps a
    range onto the plotting area, and the plotting area is the container minus
    the MARGINS. GapChart and IvChart used `r: 20`; IvDualAxis needs room for
    its right-hand Ratio axis and used `r: 58`. Same range, a plot area 38px
    narrower, so 16:01 on the bottom chart sat 38px left of 16:01 above it.

    Every previous fix went to the range, which was never what was broken --
    which is why the fix kept not working. The old Streamlit page had this
    right all along (`_SYNC_MARGIN_L/_R` in views/edge.py, with a comment
    saying why); the rebuild lost it.

    SCANNING THE SOURCE, not the rendering. A visual check needs a browser and
    a human eye, and this is precisely the fault that survived four of those.
    """
    import re

    edge = ROOT / "web" / "src" / "edge"
    offenders = []
    for name in _TIME_AXIS_CHARTS:
        source = (edge / name).read_text(encoding="utf-8")
        body = " ".join(line for line in source.splitlines()
                        if not line.lstrip().startswith("//")
                        and not line.lstrip().startswith("*"))
        # A margin written as an object literal is a private frame.
        if re.search(r"margin:\s*\{", body):
            offenders.append(f"{name} writes its own margin")
        if "TIME_AXIS_MARGIN" not in body:
            offenders.append(f"{name} does not use TIME_AXIS_MARGIN")

    assert not offenders, (
        "Calendar Edge time charts must share one frame -- "
        + "; ".join(offenders)
        + ". Import TIME_AXIS_MARGIN from './timeAxis'."
    )


def test_the_shared_frame_leaves_room_for_the_widest_axis():
    """The right margin must fit the dual-axis chart's Ratio labels.

    Sharing the frame is only half of it: shared at `r: 20` would align all
    three and clip the right-hand tick labels off the bottom one. The shared
    value has to be the widest requirement, not the narrowest -- so this pins
    the direction the three were reconciled in.
    """
    import re

    source = (ROOT / "web" / "src" / "edge" / "timeAxis.ts").read_text(encoding="utf-8")
    match = re.search(r"TIME_AXIS_MARGIN\s*=\s*\{([^}]*)\}", source)
    assert match, "TIME_AXIS_MARGIN is not an object literal any more"
    values = dict(re.findall(r"(\w+):\s*(\d+)", match.group(1)))
    assert int(values["r"]) >= 58, (
        "the right margin must fit the IV Ratio axis -- 58px was measured, "
        f"got {values['r']}")
    assert int(values["l"]) >= 58, (
        "the left margin must fit a four-digit SPX tick and its axis title")


def test_both_screens_switch_to_position_management_the_same_way():
    """A lock changes what the chart MEANS, and it must mean the same thing on
    both screens while both are live.

    `views/edge.py` has done this since long before the rebuild: on a lock it
    draws the entry as a fixed dashed line, dims the live diagonal to
    reference context under a name that says it is hypothetical, and relabels
    the fourth tooltip line to the SIGNED difference against entry. The new
    screen shipped the lock button first and none of that (Chandan,
    2026-09-07: "it was represented by the dotted line ... that is also
    missing").

    THE TWO WORDINGS ARE THE ASSERTION. A reader moving between the screens
    reads the same label for the same quantity, and a silent divergence here
    is the worst kind: both charts look right, and they answer different
    questions under the same heading.
    """
    old = (ROOT / "views" / "edge.py").read_text(encoding="utf-8")
    new = (ROOT / "web" / "src" / "edge" / "GapChart.tsx").read_text(encoding="utf-8")

    shared = ["Live Difference (vs. entry)", "Live Diagonal Mark (hypothetical)"]
    missing = [phrase for phrase in shared
               if phrase not in old or phrase not in new]
    assert not missing, (
        "the two screens disagree about what a locked position looks like: "
        f"{missing}"
    )

    # And the entry price itself is drawn, not merely named in a tooltip.
    assert "Entry $" in old and "Entry $" in new, (
        "the locked entry must be drawn as a labelled line on both screens"
    )


def test_every_panel_that_shades_volume_obeys_the_tab_toggle():
    """The volume shade is context, not measurement, and the reader may switch
    it off (Chandan, 2026-09-07: "I can either choose to have it or not").

    THE FAULT THIS PREVENTS is a seventh panel added later that draws the
    shade and never reads the toggle: the checkbox would then clear five
    panels and leave one shaded, which reads as a rendering glitch rather than
    as a control that does not reach. `chart.ts` is where the colours are
    DEFINED and draws nothing, so it is exempt by that rule rather than by
    name -- a file that only names the constants has no trace to gate.
    """
    gamma = ROOT / "web" / "src" / "gamma"
    offenders = []
    for path in sorted(gamma.glob("*.ts*")):
        text = path.read_text(encoding="utf-8")
        draws = "fillcolor: fill" in text and "VOL_FILL" in text
        if draws and "showVolume" not in text:
            offenders.append(path.name)
    assert not offenders, (
        "these panels draw the volume shade without reading the tab's "
        f"toggle: {offenders}"
    )


def test_the_toggle_cannot_put_a_volume_shade_behind_the_volume_chart():
    """`spec.shade === false` is the PANEL's refusal; `showVolume` is the
    READER's preference. They are different questions and the grid's book
    cell depends on the difference -- volume drawn behind volume is not a
    backdrop, it is the same series twice.

    Collapsing the two into one flag is the tempting simplification, so the
    check pins that MiniPanel still asks both.
    """
    text = (ROOT / "web" / "src" / "gamma" / "MiniPanel.tsx").read_text(encoding="utf-8")
    assert "spec.shade === false || !showVolume" in text, (
        "MiniPanel must refuse the shade when EITHER the panel forbids it or "
        "the reader has switched it off"
    )


def test_the_reader_steering_a_query_never_blanks_the_page():
    """Changing the expiry must not empty the screen it is being changed on.

    THE FAULT. A new selection is a new query key, which has no cached data,
    so `isPending` is true and GammaTab returned a bare "Loading the chain…"
    for the WHOLE tab — unmounting the toolbar, and with it the open dropdown.
    Chandan, 2026-09-07: "the whole page refreshes ... and the dropdown goes
    away, and I have to click on the dropdown again."

    ONE CAUSE, TWO SYMPTOMS, and the second is the one that hides the first:
    the picker has no close-on-select and never did. It was being unmounted
    along with everything else and coming back with its state reset. Anyone
    debugging the dropdown alone would find nothing wrong with it.

    `keepPreviousData` keeps the last answer on screen while the new one
    loads, so the page stays mounted. The page must then SAY that what it
    shows is the previous scope's answer, which is what `isPlaceholderData`
    is for -- and not `isFetching`, which is also true during the background
    refresh that leaves the answer correct.
    """
    client = (ROOT / "web" / "src" / "api" / "client.ts").read_text(encoding="utf-8")
    tab = (ROOT / "web" / "src" / "gamma" / "GammaTab.tsx").read_text(encoding="utf-8")

    # The two hooks the expiry picker re-keys.
    for hook in ("useGamma", "useSessionRange"):
        start = client.index(f"export function {hook}")
        body = client[start:start + 900]
        assert "...STEERED" in body, (
            f"{hook} is re-keyed by the expiry picker, so it must keep the "
            "previous answer while the new one loads (STEERED, not SHARED)"
        )

    # CODE, NOT PROSE. The first version of this line searched the whole file
    # and passed on a build where the only mention left was the comment
    # EXPLAINING isPlaceholderData -- caught by breaking it, which is the
    # entire reason breaking is the rule here.
    tab_code = " ".join(line for line in tab.splitlines()
                        if not line.lstrip().startswith(("//", "*", "/*")))
    assert "isPlaceholderData" in tab_code, (
        "the tab must say when what is drawn belongs to the previous "
        "selection -- silently showing a stale chain under a new scope is "
        "worse than blanking, because the reader believes it"
    )

    # ...AND IT MUST WAIT BEFORE SAYING IT. Chandan, 2026-09-07, on the fix
    # above: "charts go dim and say Updating", reported as the page still
    # refreshing. An expiry is answered in ~0.2s, so the dim appeared and
    # vanished inside a fifth of a second on every click -- correct, and read
    # as a flicker, which is what the change was meant to stop. A signal that
    # fires on every ordinary action is not a signal.
    assert "useSettled(restating, SETTLE_MS)" in tab_code, (
        "the stale mark must be the DELAYED form of `restating`, or it "
        "flickers on every tick of the picker (see SETTLE_MS in GammaTab). "
        "Naming the constant is not enough -- the flag actually drawn from "
        "has to be the delayed one"
    )
    for marker in ("{stale && (", "opacity: stale ?"):
        assert marker in tab_code, (
            f"expected {marker!r}: the dim and the label must both be driven "
            "by the DELAYED flag. Driving either from `restating` directly is "
            "the flicker Chandan reported"
        )
    # The delayed flag must still be derived from the immediate one. A `stale`
    # that had drifted loose of `isPlaceholderData` -- pinned to `isFetching`,
    # say -- would dim on the background refresh that leaves the answer
    # correct, which is the same flicker wearing a different cause.
    derivation = tab_code[tab_code.index("const stale ="):]
    assert derivation[:120].count("restating") == 1, (
        "`stale` must be the delayed form of `restating` and nothing else"
    )


def test_no_strike_chart_draws_the_gamma_flip_as_a_line():
    """**THE FLIP IS A NUMBER, NOT A LINE** (Chandan, 2026-09-07; ADR-056b).

    THE FAULT THIS PINS. The level is the WHOLE CHAIN's while every strike
    chart draws ONE EXPIRY's bars. A dashed vertical rule across those bars
    makes the one claim it cannot support — that the bars change character at
    that price, that "all the green turns to red" there — and no label beside
    it is loud enough to deny what the line asserts.

    Removing the drawing without pinning its absence would leave the next
    person free to add it back as an obvious improvement; the reason it is
    gone lives in an ADR, which is not where anyone looks before adding a
    `add_vline`. So the check is here, against the source.

    THE SPOT LINE IS NOT WHAT THIS FORBIDS — spot really is a property of the
    bars beneath it, and every panel still draws it. Only the flip is barred.
    """
    charts = {
        "web/src/gamma/StrikeChart.tsx": "flipStrike",
        "web/src/gamma/MiniPanel.tsx": "flipStrike",
        "views/gex.py": "flip",
    }
    for relative, name in charts.items():
        path = ROOT / relative
        assert path.exists(), f"{relative} moved -- this check now proves nothing"
        text = path.read_text(encoding="utf-8")
        # The words appear in the prose explaining WHY there is no line, so the
        # check is for the DRAWING, not for the mention.
        body = "\n".join(line for line in text.splitlines()
                          if not line.lstrip().startswith(("#", "//", "*")))
        assert f"x0: {name}" not in body, f"{relative} draws the flip again"
        assert f"add_vline(x={name}" not in body, f"{relative} draws the flip again"
