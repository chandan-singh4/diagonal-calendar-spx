"""
state — the small JSON sidecar files, and the only code that reads or writes them.

Three files, all user state rather than market data:

    chart_colors.json      your line colour choices          (~0.1 KB)
    entry_locks.json       locked entry prices per combo     (~1 KB)
    eligible_history.json  the opportunity registry          (~700 KB)

The rule for this package, enforced by tests/test_layering.py:

    state/ may import config and pandas.
    state/ may NOT import streamlit: nothing here renders or reads a widget.
    state/ may NOT import db: these files are not the database.
    Every function takes the directory as its first argument, `state_dir`.

WHY THE DIRECTORY IS AN ARGUMENT. Before M2 step 2.3 these were
`Path("eligible_history.json")` — relative, resolved against the working
directory. Launch the dashboard from anywhere but the project root and it would
find no registry, quietly create an empty one there, and render a Mission
Control panel that had forgotten everything, with no error. That was DEBT-011.
`config.STATE_DIR` is now absolute and anchored to the project root, exactly as
`config.DB_PATH` always was.

TWO GUARANTEES THE OLD CODE DID NOT HAVE, both in store.py:

  * **Writes are atomic.** The 700 KB registry is rewritten in full on every new
    snapshot. Interrupt that — crash, power cut, closed terminal — and the old
    code left a half-written file that parses as nothing.

  * **An unreadable file is never silently replaced.** The old loaders caught
    JSONDecodeError and returned `{}`. That is not a failed read, it is data
    loss on a delay: the empty dict comes back, the next update writes it out,
    and 700 KB of history is gone. store.py moves the unreadable file aside
    first, so it can still be recovered.
"""
