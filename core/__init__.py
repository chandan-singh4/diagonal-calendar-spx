"""
core — pure calculation. No database, no page, no files.

The rule for this package, enforced by tests/test_core_layering.py:

    core/ may import pandas, numpy, plotly and iv_engine.
    core/ may NOT import streamlit, db, config, or read/write anything.

Everything here is a function of its arguments alone. That is what makes it
cheap to check: no snapshot to set up, no database to stand in for, no page to
render. If something in here ever needs the database or the screen, it is in
the wrong layer — move it out rather than widening the rule.

Extracted from app.py in M2 (see docs/decisions.md, ADR-032). The names still
carry their original leading underscores so that extraction stayed a pure move
with the golden tests unchanged; renaming them is a separate, mechanical step.
"""
