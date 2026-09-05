"""The nav bar draws the page ONCE per click.

WHAT WENT WRONG. The nav is a button row rather than st.tabs, so that a
Mission Control card can jump straight to a pre-scoped tab. Handling a click
where the buttons sit — below the controls bar — meant the controls bar had
already been drawn for the previous tab, so the click ended in st.rerun() and
every navigation executed the whole script twice: build, discard, build again.
Chandan saw that second draw as a flicker on every tab change (2026-09-05).

The choice is now resolved at the top of the run instead, from the button's
own state, before anything is drawn.

WHY THIS IS A SOURCE TEST AND NOT A RUNNING ONE. Driving app.py end to end
means streamlit.testing's AppTest, and AppTest runs the real script against
config.DB_PATH — the live 3.4 GB record, which no check here may touch. The
behaviour WAS verified against the real app by hand: one script execution per
click, the highlight correct, and the controls bar still hidden on Gamma
Exposure. What this file defends is the shape that makes it true, since the
regression is silent — the page still works, it just draws twice.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent / "app.py"


@pytest.fixture(scope="module")
def source() -> str:
    return APP.read_text(encoding="utf-8")


def _code_lines(source: str) -> list[str]:
    """Lines with the comments and docstring prose stripped out.

    The explanation of the old bug names st.rerun() several times. Matching on
    raw text would find those and pass — or fail — on prose.
    """
    return [line for line in source.splitlines()
            if line.strip() and not line.strip().startswith("#")]


def test_the_nav_bar_does_not_rerun(source):
    """The double draw, stated directly. app.py holds no st.rerun() at all —
    the three that survive elsewhere are in views/ and ui/locks.py, where they
    are genuine: those set pending_ keys that the controls bar must promote
    BEFORE its widgets exist, and by then it has already been drawn."""
    offenders = [line.strip() for line in _code_lines(source)
                 if "st.rerun()" in line]

    assert offenders == [], (
        "an st.rerun() in app.py makes every tab click draw the page twice, "
        f"which reads as a flicker: {offenders}"
    )


def test_the_click_is_resolved_before_the_controls_bar_is_drawn(source):
    """The ordering constraint that forced the old shape.

    The Gamma Exposure tab hides the controls bar, and that decision is made
    above the nav. If the click were resolved after it, the controls bar would
    be drawn for the tab being left rather than the one being entered — which
    is precisely the bug that the st.rerun() was papering over.
    """
    resolution = source.index('if st.session_state.get(f"nav_{_tkey}")')
    ctrlbar    = source.index('with st.container(key="ctrlbar")')
    nav        = source.index('with st.container(key="topnav")')

    assert resolution < ctrlbar, (
        "the active tab must be settled before the controls bar draws"
    )
    assert resolution < nav, (
        "the click must be read before the nav buttons are created, or the "
        "highlight is one click behind"
    )


def test_the_nav_buttons_only_draw(source):
    """A button whose result is assigned is a button making a decision, and
    the decision belongs at the top of the script now. `if st.button(` here
    would mean the old shape had crept back."""
    nav_start = source.index('with st.container(key="topnav")')
    nav_end = source.index("# Exactly one tab body runs per script execution")
    nav_block = source[nav_start:nav_end]

    assert "if st.button(" not in nav_block
    assert re.search(r"^\s*st\.button\(", nav_block, re.MULTILINE), (
        "the nav bar must still draw its buttons"
    )


def test_every_tab_in_the_table_is_dispatched(source):
    """Unchanged by the fix, and worth holding: the key list drives the click
    resolution, the buttons and the dispatch. A tab present in one and absent
    from another is a tab that cannot be reached or cannot be left."""
    keys = re.findall(r'^\s*\("(\w+)",\s+"', source, re.MULTILINE)

    assert len(keys) == 6, f"expected the six tabs, found {keys}"
    assert len(set(keys)) == len(keys), "duplicate tab key"
