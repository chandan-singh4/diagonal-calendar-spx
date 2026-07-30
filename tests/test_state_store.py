"""
The two guarantees the sidecar files did not used to have.

These files are not in version control (all four are gitignored) and the
registry is ~700 KB rewritten in full on every new snapshot. That combination is
why these tests exist rather than being filed under tidiness: there is no other
copy, and the write happens roughly 126 times a trading day.

WHAT IS PINNED HERE
  1. Paths are absolute and independent of the working directory  (DEBT-011)
  2. A write is all-or-nothing
  3. An unreadable file is moved aside, never silently replaced

Number 3 is the one that was actively dangerous. The old loaders caught
JSONDecodeError and returned {}. That is not a failed read — it is data loss on
a delay: the empty dict comes back, Mission Control writes it out on the next
snapshot, and the history is gone with nothing to recover from.
"""
from __future__ import annotations

import json

import pytest

from state import store

pytestmark = pytest.mark.integration

FILENAME = "thing.json"


# ─────────────────────────────────────────────────────────────────────────────
# 1. Where the file lives does not depend on where you were standing
# ─────────────────────────────────────────────────────────────────────────────

def test_the_path_is_absolute_and_ignores_the_working_directory(tmp_path, monkeypatch):
    """The DEBT-011 proof. Same state_dir, two different working directories,
    one answer — because that is exactly what used to differ."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    monkeypatch.chdir(tmp_path)
    from_here = store.resolve(state_dir, FILENAME)

    monkeypatch.chdir(elsewhere)
    from_there = store.resolve(state_dir, FILENAME)

    assert from_here == from_there
    assert from_here.is_absolute()


def test_a_missing_file_reads_as_the_default_and_creates_nothing(tmp_path):
    """A first run must not be an error, and must not leave a file behind."""
    assert store.read_json(tmp_path, FILENAME) == {}
    assert store.read_json(tmp_path, FILENAME, default={"a": 1}) == {"a": 1}
    assert not (tmp_path / FILENAME).exists()


def test_a_round_trip_returns_what_was_written(tmp_path):
    store.write_json(tmp_path, FILENAME, {"colour": "#5b9cff", "n": 3})
    assert store.read_json(tmp_path, FILENAME) == {"colour": "#5b9cff", "n": 3}


# ─────────────────────────────────────────────────────────────────────────────
# 2. All-or-nothing writes
# ─────────────────────────────────────────────────────────────────────────────

def test_the_write_goes_via_a_temporary_file_and_a_rename(tmp_path, monkeypatch):
    """Atomicity is a property of the MECHANISM, so the mechanism is what this
    asserts — deliberately, and it is the only test here that does.

    There is no way to observe it from the outside: you cannot pull the power
    out mid-write from a test. And the obvious behavioural test is not enough —
    an unserialisable payload raises BEFORE any bytes are written, so a plain
    `path.write_text(json.dumps(...))` passes it too. Since the file being
    protected is ~700 KB, rewritten ~126 times a trading day, with no copy in
    version control, asserting the rename is worth the coupling.
    """
    calls = []
    real_replace = store.os.replace

    def spy(src, dst):
        calls.append((str(src), str(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr(store.os, "replace", spy)
    store.write_json(tmp_path, FILENAME, {"a": 1})

    assert len(calls) == 1, "the file was not put in place by a single rename"
    src, dst = calls[0]
    assert src.endswith(".tmp"), f"renamed from {src}, not from a temporary file"
    assert dst.endswith(FILENAME)


def test_a_failed_write_leaves_the_previous_file_intact(tmp_path):
    """A payload that cannot be serialised must leave the old file alone and say
    so, rather than half-writing or reporting success."""
    store.write_json(tmp_path, FILENAME, {"keep": "me"})

    ok = store.write_json(tmp_path, FILENAME, {"bad": {1, 2, 3}})  # a set: not JSON

    assert ok is False
    assert store.read_json(tmp_path, FILENAME) == {"keep": "me"}


def test_the_original_survives_a_failure_during_the_rename(tmp_path, monkeypatch):
    """The interruption that atomicity actually protects against: everything
    serialised and written, then the final step fails."""
    store.write_json(tmp_path, FILENAME, {"keep": "me"})

    def boom(src, dst):
        raise OSError("interrupted")

    monkeypatch.setattr(store.os, "replace", boom)
    ok = store.write_json(tmp_path, FILENAME, {"new": "value"})

    assert ok is False
    assert store.read_json(tmp_path, FILENAME) == {"keep": "me"}
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]


def test_a_failed_write_does_not_leave_a_temporary_file_behind(tmp_path):
    store.write_json(tmp_path, FILENAME, {"keep": "me"})
    store.write_json(tmp_path, FILENAME, {"bad": {1, 2, 3}})

    leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert not leftovers, f"temporary files left behind: {leftovers}"


def test_a_successful_write_leaves_only_the_target_file(tmp_path):
    store.write_json(tmp_path, FILENAME, {"a": 1})

    assert sorted(p.name for p in tmp_path.iterdir()) == [FILENAME]


# ─────────────────────────────────────────────────────────────────────────────
# 3. An unreadable file is quarantined, never overwritten
# ─────────────────────────────────────────────────────────────────────────────

def test_unreadable_content_is_moved_aside_rather_than_lost(tmp_path):
    """The important one. Truncated JSON must not read as "no history" and then
    get overwritten by the next save — that turns one bad parse into permanent
    data loss."""
    (tmp_path / FILENAME).write_text('{"half": "a fi', encoding="utf-8")

    assert store.read_json(tmp_path, FILENAME) == {}

    quarantined = [p for p in tmp_path.iterdir() if ".corrupt-" in p.name]
    assert len(quarantined) == 1, f"expected exactly one quarantined file, got {quarantined}"
    assert quarantined[0].read_text(encoding="utf-8") == '{"half": "a fi'
    assert not (tmp_path / FILENAME).exists(), (
        "the unreadable file is still in place — the next write would destroy it"
    )


def test_json_that_is_not_an_object_is_also_quarantined(tmp_path):
    """A list parses fine but is not a registry. Returning it would fail later,
    somewhere less obvious."""
    (tmp_path / FILENAME).write_text(json.dumps([1, 2, 3]), encoding="utf-8")

    assert store.read_json(tmp_path, FILENAME) == {}
    assert any(".corrupt-" in p.name for p in tmp_path.iterdir())


def test_the_pipeline_loader_refuses_to_run_against_the_real_state_dir():
    """The guard that stops a test run destroying the real registry.

    `_update_eligible_history` WRITES. With config.STATE_DIR left at its default
    — the project root — a pipeline test would overwrite your actual ~700 KB
    history. Nothing patches STATE_DIR here, so load_pipeline() must refuse.
    """
    from app_loader import load_pipeline

    with pytest.raises(AssertionError, match="STATE_DIR"):
        load_pipeline()


def test_quarantine_survives_a_second_bad_file(tmp_path):
    """Two corruptions must not have the second silently replace the first
    rescue copy."""
    (tmp_path / FILENAME).write_text("nonsense one", encoding="utf-8")
    store.read_json(tmp_path, FILENAME)
    (tmp_path / FILENAME).write_text("nonsense two", encoding="utf-8")
    store.read_json(tmp_path, FILENAME)

    rescued = sorted(p.read_text(encoding="utf-8")
                     for p in tmp_path.iterdir() if ".corrupt-" in p.name)
    assert rescued == ["nonsense one", "nonsense two"]
