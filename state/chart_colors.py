"""Your line colour choices — a user preference, not market data."""
from __future__ import annotations

from state.store import read_json, write_json

FILENAME = "chart_colors.json"

# To add colour customization for a future line series: add one entry here as a
# (label, hex) tuple. The sidebar picker and the reset button both iterate this
# dict automatically — no other code changes needed beyond using
# CHART_COLORS["your_key"] in the relevant trace.
DEFAULT_CHART_COLORS: dict[str, tuple[str, str]] = {
    "diagonal_mark":  ("Diagonal Mark",        "#5b9cff"),
    "transform_mark": ("Transform Order Mark", "#f0a429"),
    "front_iv":       ("Front IV %",           "#10d4a3"),
    "back_iv":        ("Back IV %",            "#5b9cff"),
}


def load(state_dir) -> dict[str, str]:
    """Saved colours, with any missing or newly-added key filled from defaults.

    Unknown keys in the file are dropped rather than passed through: a stale
    entry for a series that no longer exists should not reach a trace.
    """
    defaults = {k: v[1] for k, v in DEFAULT_CHART_COLORS.items()}
    saved = read_json(state_dir, FILENAME)
    defaults.update({k: v for k, v in saved.items() if k in defaults})
    return defaults


def save(state_dir, colors: dict[str, str]) -> bool:
    return write_json(state_dir, FILENAME, colors)
