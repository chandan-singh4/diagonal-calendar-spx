"""One module per tab, extracted from app.py in M2 step 2.4.

A view DRAWS. It is handed everything it needs — the snapshot, the current
selection, the derived numbers, and any memoised loader it calls — on a
`ViewContext`, and it reaches for nothing else. It must never open the
database itself: the `@st.cache_data` wrappers live in app.py (ADR-032), and
a view importing `dataaccess` directly would bypass them and re-query on
every rerun while still looking correct.

This is the least-tested layer in the repo and the one that cannot really be
tested, which is why it is extracted LAST and why each move is a verbatim
move — see the note in each module's `render()`.
"""
