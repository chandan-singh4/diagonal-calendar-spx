"""
Load the pure P&L functions out of pages/journal.py without running the page.

WHY THIS EXISTS
---------------
pages/journal.py is a Streamlit script: importing it calls st.set_page_config(),
queries the production database, and renders the whole journal. None of that is
acceptable inside a unit test — it is slow, it needs a browser session, and it
would read (and lock) the 1.4 GB dashboard.db.

But the P&L maths inside that file handles real money and is the highest-value
thing in the repo to test. Waiting for M2 to extract it into core/ means the
extraction itself would be unverified — exactly the situation M1 exists to
prevent.

So this loader parses journal.py's AST, pulls out ONLY the named function
definitions, and executes those definitions in a clean namespace. It runs the
real function bodies from the real file: if someone edits the maths in
journal.py, these tests see the edit. It skips every module-level statement, so
no Streamlit call and no database access ever happens.

This is deliberately temporary. When M2 extracts these into a framework-free
module, this file is deleted and the tests change one import line — nothing
else. The test assertions are written against function behaviour, not against
this loading mechanism, precisely so that swap is trivial.

LIMITS (stated so nobody over-trusts this)
  - Only the requested functions and their in-file callees are loaded.
  - Decorators referencing Streamlit (e.g. @st.cache_data) are stripped; the
    functions under test carry none today, and load_journal_functions() raises
    if that ever changes rather than silently testing undecorated code.
"""
from __future__ import annotations

import ast
import json
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

JOURNAL_PATH = Path(__file__).resolve().parent.parent / "pages" / "journal.py"

# Every function below is either directly under test or called by one that is.
# Listed explicitly rather than "load everything that looks pure" so that a new
# dependency appearing in journal.py surfaces as a clear NameError in a test,
# not as a silently different code path.
_WANTED = (
    "row_get",
    "get_close_type",
    "total_fees",
    "holding_days",
    "ic_expiry_pnl_per_share",
    "auto_final_pl",
    "resolved_pl",
    "derive_ic",
    "compute_stats",
    "_stored_entry_iv",
)


def load_journal_functions() -> dict:
    """Return {name: function} for the pure P&L helpers in pages/journal.py."""
    tree = ast.parse(JOURNAL_PATH.read_text(encoding="utf-8"), filename=str(JOURNAL_PATH))

    wanted = set(_WANTED)
    picked: list[ast.FunctionDef] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            if node.decorator_list:
                raise AssertionError(
                    f"{node.name}() gained a decorator ({ast.unparse(node.decorator_list[0])}). "
                    f"Stripping it would test different code than production runs. "
                    f"Update tests/journal_loader.py deliberately."
                )
            picked.append(node)
            wanted.discard(node.name)

    if wanted:
        raise AssertionError(
            f"pages/journal.py no longer defines: {sorted(wanted)}. "
            f"If these moved (M2 extraction), update the tests to import them directly."
        )

    # Namespace holds only what the extracted bodies actually reference. Note
    # what is absent: streamlit, db, config. If a function under test ever grows
    # a dependency on those, it stops being pure and the test fails loudly with
    # a NameError — which is the correct signal, not something to paper over.
    namespace: dict = {
        "json": json,
        "date": date,
        "datetime": datetime,
        "np": np,
        "pd": pd,
        "__builtins__": __builtins__,
    }

    module = ast.Module(body=picked, type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, filename=str(JOURNAL_PATH), mode="exec"), namespace)

    return {name: namespace[name] for name in _WANTED}
