"""Read and write the JSON sidecar files safely.

Every other module in `state/` goes through these three functions rather than
touching the filesystem itself, so the atomic-write and quarantine guarantees
hold for all three files rather than whichever one someone remembered.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def resolve(state_dir, filename: str) -> Path:
    """The absolute path of one sidecar file.

    Absolute is the entire point — see the package docstring and DEBT-011. A
    relative `state_dir` is accepted and resolved rather than rejected, because
    the caller that passes one is usually a test using tmp_path.
    """
    return Path(state_dir).resolve() / filename


def read_json(state_dir, filename: str, *, default: dict | None = None) -> dict:
    """Load a sidecar file, returning `default` ({}) when it does not exist.

    An UNREADABLE file is a different thing from a missing one, and is treated
    as such: it is renamed out of the way before the default is returned, so the
    next write cannot overwrite it. The old behaviour — catch, return {}, let the
    caller save {} back — turned one bad parse into permanent data loss.
    """
    path = resolve(state_dir, filename)
    fallback = {} if default is None else dict(default)

    if not path.exists():
        return fallback

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        quarantined = _quarantine(path)
        logger.error(
            "%s could not be read (%s). Moved to %s so it is not overwritten; "
            "continuing with an empty %s.",
            path, exc, quarantined.name if quarantined else "<move failed>", filename,
        )
        return fallback

    if not isinstance(data, dict):
        quarantined = _quarantine(path)
        logger.error(
            "%s contained %s, not an object. Moved to %s; continuing empty.",
            path, type(data).__name__, quarantined.name if quarantined else "<move failed>",
        )
        return fallback

    return data


def write_json(state_dir, filename: str, payload: dict) -> bool:
    """Write a sidecar file atomically. Returns True on success.

    Written to a temporary file in the SAME directory and then renamed over the
    target. `os.replace` is atomic, so a reader sees either the whole old file or
    the whole new one, never a half-written one. Same directory matters: a rename
    across volumes is a copy, which is not atomic.

    Failures are logged and reported, not raised — a dashboard that cannot save
    your chart colours should keep drawing charts. The bool lets a caller that
    does care check.
    """
    path = resolve(state_dir, filename)
    tmp = path.with_name(path.name + ".tmp")

    try:
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except (OSError, TypeError, ValueError):
        logger.exception("could not write %s", path)
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        return False


def _quarantine(path: Path) -> Path | None:
    """Move an unreadable file aside, timestamped. Returns the new path.

    The name is made unique before the move. `os.replace` overwrites its target
    silently, so a second corruption within the same second would otherwise
    destroy the first rescue copy — which defeats the entire point of moving it
    aside. Found by test_quarantine_survives_a_second_bad_file, which failed on
    the first version of this function.
    """
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    target = path.with_name(f"{path.name}.corrupt-{stamp}")
    suffix = 1
    while target.exists():
        target = path.with_name(f"{path.name}.corrupt-{stamp}-{suffix}")
        suffix += 1

    try:
        os.replace(path, target)
        return target
    except OSError:
        logger.exception("could not quarantine %s", path)
        return None
