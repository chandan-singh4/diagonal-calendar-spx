"""Design System v3.4 — the stylesheet, injected once per script run.

WHY THE CSS IS NOT IN app.py ANY MORE. It was 757 of that file's 2,505 lines
— the single largest thing in it, and the least related to anything else
there. It is also not Python: kept in a triple-quoted string it got no
syntax highlighting, no brace matching and no CSS linting, and a stylesheet
fails SILENTLY. One dropped brace kills every rule after it and raises
nothing.

WHY A .css FILE RATHER THAN A PYTHON CONSTANT. Same reason, plus one: the
file is now editable without touching a `.py`, and Streamlit only reloads on
`.py` saves — so editing the theme no longer risks a mid-analysis rerun of
the whole page.

THE PATH IS BUILT FROM __file__, NOT THE WORKING DIRECTORY. `Path("assets/
theme.css")` resolves against wherever the dashboard was launched from, which
is DEBT-011 exactly — the defect that made the sidecar JSON files vanish when
the app was started from another directory (ADR-035). Here the failure would
be louder (an unstyled page rather than silent data loss), but the rule is the
rule.

Design principles the stylesheet encodes, unchanged from v3.4:
  1. SPACE is hierarchy — sections breathe; no decorative dividers.
  2. SIZE is hierarchy — critical values 3x larger than labels.
  3. COLOR signals STATE — green/red/amber only for meaning.
  4. ONE bold move — the pulsing green KPI card when diff >= 5.
     Everything else is quiet and disciplined around it.
"""
from __future__ import annotations

from pathlib import Path

import streamlit as st

STYLESHEET = Path(__file__).resolve().parent.parent / "assets" / "theme.css"


def css() -> str:
    """The stylesheet's text. Separate from apply() so a test can read it
    without a Streamlit runtime."""
    return STYLESHEET.read_text(encoding="utf-8")


def apply() -> None:
    """Inject the stylesheet.

    The wrapper reproduces app.py's original `st.markdown` argument character
    for character — leading newline, `<style>` tags, trailing newline. That
    was checked by reconstruction when the block was lifted out, and it is
    what let the before/after render comparison come back identical rather
    than showing a whitespace difference on every single page.
    """
    st.markdown(f"\n<style>\n{css()[:-1]}\n</style>\n", unsafe_allow_html=True)
