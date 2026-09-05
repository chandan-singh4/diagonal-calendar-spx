"""
api — the price history, served over HTTP. No page.

The rule for this package, enforced by tests/test_layering.py:

    api/ may import fastapi, pydantic, core, dataaccess, db, config, pandas.
    api/ may NOT import streamlit, services, views, ui, or app.

THE `services` CLAUSE IS THE LOAD-BEARING ONE, and it is the reason this
package exists rather than the API being bolted onto what is already here.
`services/` is the only layer permitted both `config` and `streamlit`; every
module in it imports the page. The moment anything under `api/` imports
`services`, this package has imported Streamlit — and a server that needs a
browser session to answer a question is not a server. The failure would be
quiet, too: `st.cache_data` outside a script run does not raise, it degrades.

WHAT THIS LAYER IS FOR. Exactly what `services/` is for on the page side, and
nothing more: binding the process-level facts — which database, which cache —
so that `dataaccess/` stays told-what-to-read and `core/` stays pure. It is
the only layer besides `services/` that may name `config.DB_PATH`.

READ-ONLY, FOR NOW AND ON PURPOSE. Every read below goes through
`dataaccess/`, which opens the database with `PRAGMA query_only=ON`. The
collector is writing to the same file while this process reads it. M4.3 is
expected to add the one exception — the Mission Control "New" registry needs
to persist across restarts and across clients (Chandan's decision, 2026-09-05)
— and it will be the only write in this package. Adding a second one is a
decision, not a detail.

M4 DOES NOT TOUCH THE DASHBOARD. `app.py`, `views/`, `ui/` and `services/` are
unchanged by this milestone and must stay that way; the exit condition is that
the Streamlit app still runs, behaving identically, beside this server reading
the same database. If finishing something here appears to require editing the
page, the scope is wrong — see docs/m4_plan.md.
"""
