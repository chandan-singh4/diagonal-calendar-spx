"""Page chrome — everything app.py draws that is not a tab.

WHY A SECOND DRAWING LAYER. `views/` holds the six tabs, and every module in
it answers to one rule: expose `render(ctx)` and take nothing else. The header
bar, the sidebar, the Position Controls bar and the theme are not tabs. They
run once per script execution, before any tab is chosen, and several of them
RETURN the values the tabs are later handed — the controls bar is where
`front_expiry` comes from. Forcing them into `views/` would have meant
loosening that rule for everyone.

THE RULE HERE INSTEAD: a ui/ module is told everything. It may draw, and it
may read widget state, but it does not fetch. No `db`, no `dataaccess`, no
`schwab_client`, and no `config.DB_PATH` / `config.STATE_DIR` — the same
attributes `views/` is barred from, for the same reason (a module that can
find the database is a module that can query it, bypassing app.py's memos
with no visible symptom). Enforced in tests/test_layering.py.
"""
