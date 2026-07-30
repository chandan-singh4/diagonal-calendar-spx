"""Run every dashboard tab and report anything it throws.

WHY THIS EXISTS. On 2026-07-30, during M2 step 2.2, `_exp_label` gained a
required argument. Two call sites were updated. A third use was not — the
function is passed to Streamlit by REFERENCE as a selectbox `format_func`, so
grepping for `_exp_label(` never saw it. **All 569 tests passed and every tab of
the dashboard raised TypeError on load.** This script caught it; nothing else
did, and nothing else could.

The gap is structural, not an oversight in the suite. The tests exercise
functions; they cannot exercise the page, because the page's module level reads
the production database and renders six tabs. Until `views/` exists (M2 step 2.4)
that will remain true, so run this after ANY change to app.py.

WHY NOT `curl localhost:8501`. Streamlit only executes the script when a client
session connects. Fetching the HTML proves the server is up and nothing else.
`AppTest` actually runs it, top to bottom.

WHY EVERY TAB. The tabs are custom buttons, not `st.tabs`, so one run renders
only the active tab. Six runs, one per tab, is the difference between covering a
sixth of the page and covering all of it.

TWO SIDE EFFECTS, both the same ones opening the dashboard normally has:
  * it READS the production database (read-only, like the dashboard)
  * it REWRITES eligible_history.json in the repo root

Usage:  python scripts/render_check.py        (exit 0 = every tab rendered)
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# `streamlit run` puts the script's directory on sys.path; AppTest does not,
# so `import config` inside app.py fails without this.
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest

APP = ROOT / "app.py"
TABS = ["scanner", "entry", "edge", "strike", "hist", "research"]
TIMEOUT_SECONDS = 300


def main() -> int:
    failures: list[tuple[str, str, list[str]]] = []

    for tab in TABS:
        at = AppTest.from_file(str(APP), default_timeout=TIMEOUT_SECONDS)
        at.run()
        if at.exception:
            failures.append((tab, "initial run", [e.value for e in at.exception]))
            print(f"{tab:<10} FAILED before any tab was even selected")
            continue

        at.button(key=f"nav_{tab}").click().run()
        if at.exception:
            failures.append((tab, "after click", [e.value for e in at.exception]))
            print(f"{tab:<10} EXCEPTION: {[e.value for e in at.exception]}")
            continue

        errors = [e.value[:120] for e in at.error]
        status = "ok" if not errors else f"st.error x{len(errors)}"
        print(f"{tab:<10} {status:<14} markdown={len(at.markdown):<4} "
              f"df={len(at.dataframe):<3} buttons={len(at.button):<4} "
              f"selects={len(at.selectbox)}")
        for e in errors:
            print(f"           {e}")

    print()
    if failures:
        print(f"{len(failures)} TAB(S) RAISED:")
        for tab, when, exc in failures:
            print(f"  {tab} ({when}): {exc}")
        return 1

    print("All six tabs executed with no exception.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
