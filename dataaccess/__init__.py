"""
dataaccess — every read of the price history, in one place.

NAMED `dataaccess/`, NOT `data/`. The plan said `data/`, but that directory
already exists and holds `dashboard.db` (1.57 GB of irreplaceable market data)
and `token.json` (broker credentials). Only `.gitkeep` in it is tracked. Putting
source code beside those is a bad idea for obvious reasons, so the package took
a different name. See ADR-033.

The rule for this package, enforced by tests/test_core_layering.py:

    dataaccess/ may import db, config, pandas — it exists to read the database.
    dataaccess/ may NOT import streamlit: nothing here renders, caches, or
    reads a widget.
    dataaccess/ may NEVER reference `config.DB_PATH`.

That last one is the point of the layer, not a detail. Every function here takes
`db_path` as its first argument. Before M2 these reads took the database
location from a module global, so no caller could aim them anywhere else and
every test had to overwrite `config.DB_PATH` — a test modifying the thing it is
testing (DEBT-027).

ON MEMOISATION. The `@st.cache_data` wrappers stay in `app.py`, for the same
reason as `core/`: Streamlit cannot be imported here. `app.py` keeps a thin
`_load_*` function per query that applies the memo and supplies `config.DB_PATH`.
The `snapshot_id` arguments live there too — they were only ever cache keys, and
none of them was ever read by the query itself.
"""
