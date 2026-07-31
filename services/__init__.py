"""The page's own data layer — memoisation, sidecar binding, Mission Control.

WHY A FIFTH LAYER, AND WHY IT IS NOT ONE OF THE FOUR THAT EXIST. What lives
here could not go anywhere else without breaking a rule that is load-bearing:

  * NOT `core/` — every module here touches `streamlit`. The memoised
    loaders ARE `@st.cache_data`, and `_run_mission_control` reads and writes
    `st.session_state` to work out which opportunities are new since the last
    snapshot. core/ is barred from streamlit precisely so it stays checkable
    without a page.
  * NOT `dataaccess/` — that layer is told which database and returns rows.
    These wrappers DECIDE the database (`config.DB_PATH`) and cache the
    result. Putting the cache there would make the reads untestable again,
    which is DEBT-027 running backwards.
  * NOT `views/` — none of this draws, and it runs on every script execution
    regardless of which tab is open. The header's Attention Strip needs
    Mission Control, so it cannot belong to the Scanner tab.

WHAT THE LAYER IS FOR: binding the page-level facts — which database, which
state directory, which cache — so that everything below stays pure and
everything above stays presentational. It is the only layer that may import
BOTH `config` and `streamlit`.

THE SEAMS ARE THE WHOLE POINT. `compute_transform_scanner` here is the
MEMOISED scanner and `core.scanner`'s is the pure one; they return identical
rows, and calling the wrong one re-runs 21 offset sweeps on every rerun with
no visible symptom. Same for `load=` in `dataaccess/`. That class of fault
survived the step 2.1 mutation run (ADR-032), so it is asserted against the
source in tests/test_layering.py rather than trusted to review.
"""
