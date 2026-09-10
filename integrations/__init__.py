"""
integrations/ — outbound network, and nothing that knows about this project.

WHY THIS IS NOT services/. `services/` is defined as the Streamlit page's data
layer: it is the one layer allowed both `config` and `streamlit`, and
`tests/test_layering.py` forbids `api/` from importing it for exactly that
reason — every module in it drags the page into a process serving HTTP.
`llm.py` and `telegram.py` were sitting there and import neither. They were
misfiled, and the misfiling only became visible when the FastAPI side needed
the model chain (2026-09-08).

THE RULE HERE. A module in this package talks to something outside the
machine and reads its credentials from the environment. It imports no config,
no database, no page and no view, so any layer may call it: the collector, a
script, a Streamlit service, or a FastAPI route. That is what makes it safe to
put underneath both faces of the dashboard at once.
"""
