# TROUBLESHOOTING.md — when something is wrong

Start here. Find the symptom, not the cause.

**One rule before any of it: when the written record and the system disagree, believe the system.**
This project's notes have been wrong about the running state four times, and every time the
database or the machine had the right answer. Check before acting on a note — including a note in
this file.

**Second rule: nothing here needs a hurry.** The prices already collected are safe. The only truly
time-critical failure is one that stops collection *during market hours*, and even then the cost
is bounded by how quickly you notice.

| Symptom | Jump to |
|---|---|
| Email or pop-up: "No prices for … — collection has stopped" | [1](#1-the-watchdog-says-collection-has-stopped) |
| Email or pop-up about the broker permission expiring | [2](#2-the-broker-permission-has-expired-or-is-about-to) |
| Dashboard shows a red TOKEN EXPIRED banner | [2](#2-the-broker-permission-has-expired-or-is-about-to) |
| A gap in the data / a day looks short | [3](#3-there-is-a-gap-in-the-record) |
| `collector.log` says rows were REFUSED BY THE DATABASE | [4](#4-the-log-says-rows-were-refused-by-the-database) |
| `collector.log` says rows were duplicates | [4](#4-the-log-says-rows-were-refused-by-the-database) |
| The numbers on screen look wrong | [5](#5-the-numbers-look-wrong) |
| "database is locked" | [6](#6-database-is-locked) |
| Disk is filling up | [7](#7-the-disk-is-filling-up) |
| A fix was made and nothing changed | [8](#8-a-fix-was-made-and-nothing-changed) |
| The watchdog itself died | [9](#9-the-watchdog-itself-failed) |

---

## 1. The watchdog says collection has stopped

**What it means.** No new prices have arrived for more than 2.5 times the interval the collector
should be using right now. One slow poll will not trigger it; two will.

**What it does not mean.** It does not mean anything is damaged. Everything already collected is
untouched.

**Check, in this order:**

1. **Is the market actually open?** The watchdog knows the calendar, but confirm — it says
   "Market closed — collector idle by design" when nothing is expected.
2. **Is the process running?** Look for `python.exe` running `collector.py` in Task Manager.
3. **If it is not running:** restart it, either by opening `start_collector.bat` or by logging out
   and back in (it starts from a Startup-folder shortcut). Then confirm a new snapshot appears
   within a couple of minutes: `python scripts/check_db.py`.
4. **If it *is* running but nothing is arriving:** look at the end of `collector.log`. The usual
   cause is the broker permission — go to section 2. On 19 August it was four consecutive cycle
   failures from a code error, and the log said so plainly.

**The alert repeats at most once an hour** for the same ongoing outage. That is deliberate: a
five-minute check would otherwise turn one dead collector into twelve emails an hour, and you
would learn to delete them unread.

---

## 2. The broker permission has expired, or is about to

**What it means.** Schwab's authorisation lasts 7 days. When it lapses the collector can still
run but gets nothing back.

**Fix:**

```
python scripts/reauth.py
```

Follow `RUNBOOK_REAUTH.md`. It restores the old permission if you abort or it fails, so starting
it is safe.

**Do not** run `python -c "import schwab_client; schwab_client.get_client()"`. That is the obvious
command and it does not work: `get_client()` only starts a login when the token file is *absent*,
and the stale file is still there right up until it expires. It loads the dead token, prints
nothing, and you discover the problem on Monday morning. This trap cost a session once already.

**After re-authorising, check that prices resume** — `python scripts/check_db.py`. Do not assume.

---

## 3. There is a gap in the record

**First: is it already explained?** The collector detects its own outages and writes them to
`collection_gaps` with a reason. Two reasons appear:

- **`MARKET_CLOSED`** — expected. Nights, weekends, holidays.
- **`COLLECTOR_OFFLINE`** — the collector was not running during market hours.

```
python scripts/check_db.py     # shows recorded gaps
python scripts/audit.py        # reconciles short days against them
```

**A short day the collector already owned up to is a note, not a problem** — the gap detector
working is not a fault. A short day with **no** recorded gap is an alarm: something was lost and
nothing noticed.

**A day of 127 instead of 128 snapshots is usually a restart**, not a loss. Restarting shifts the
polling cadence by a minute or two, which can drop one five-minute slot.

**The two known ten-week holes are history, not faults:** every day before 3 September has no
price at or after 16:00 (the window ended too early — ADR-049), and every third Friday before
19 August holds only one of the two contracts (ADR-046). The audit files both as history. Neither
is recoverable, and nothing can be done about them.

---

## 4. The log says rows were REFUSED BY THE DATABASE

Two different messages, and the difference matters (ADR-050).

**"… rows were duplicates and were dropped … Nothing is missing"** — WARNING, benign. The
contract that was dropped is identical to the one that was kept. Nothing to do.

**"… rows were REFUSED BY THE DATABASE and those prices are GONE"** — ERROR, and it is real. Those
contracts are absent from the table and cannot be re-fetched. The message names SQLite's own
reason and identifies the first offending contract.

**What to do:** read the reason. The one to fear is a `CHECK constraint failed` appearing on
*every* row, which would mean the broker changed a convention — for instance sending `CALL` where
it used to send `C`. That is total, silent loss of a session, and it needs the parser fixed and
the collector restarted the same day.

**After ADR-046 the expected number of discards is zero**, so either message appearing at all is
worth a look. That is the point of the change: for eight weeks a single warning fired 2,181 times
with an identical count, and nobody read it. If you find yourself ignoring one of these because
you see it every day, that is the bug, not the noise.

---

## 5. The numbers look wrong

**Run the audit before theorising:**

```
python scripts/audit.py --deep
```

It checks the closing price, both third-Friday contracts, day completeness, volatility sanity,
missing legs and stale quotes. It reads the real record, not a test copy.

**Known things that are not bugs:**

- A **blank** volatility or greek means the broker had no value to give — usually at 09:30 on a
  contract that has not traded yet. Blank is correct. It is not zero.
- **Negative theta is normal.** An option losing $9.99 a day shows `-9.99`, and 38 such rows are
  real data. Do not "clean" them.
- Before 19 August, a third-Friday row has a **blank settlement**. That means "not recorded", not
  "morning".

**Known things that are bugs, already logged:** on expiry day one tile says "set strikes" when
they are already set (BUG-018); the front expiry defaults to 0 DTE, so there is no straddle to
normalise against and the message points at the wrong control.

**If a value looks impossible**, check it against the raw record before changing any code. The
written record has been wrong more often than the data has.

---

## 6. "database is locked"

**What it means.** Something is holding the write lock longer than the 15 seconds `db.py` waits.

**Almost always the cause is a long write of your own.** The database is in WAL mode, so ordinary
reading never blocks anything. But a single `UPDATE` with a `WHERE` on an unindexed column scans
18.8 million rows and holds the lock for about 50 seconds — long enough to fail a live poll.

**Fix:** never write to the live database with a scanning statement. Find the row ids on a
read-only connection first, then write by primary key. `scripts/repair_bug030.py` does exactly
this and commits in a tenth of a second.

**Do not** delete the `-wal` or `-shm` files to "clear" a lock. They hold recent writes.

---

## 7. The disk is filling up

The record grows about **82 MB per trading day**, roughly 20 GB a year.

```
python scripts/prune.py             # reports what could go. Deletes nothing.
python scripts/prune.py --execute   # deletes, after you type the row count
```

Per-strike option rows are prunable 90 days after their expiry. Summaries, snapshots, gaps and the
journal are kept forever. Any expiry a real trade used is exempt at any age.

**Deleting does not shrink the file.** SQLite keeps the freed pages for reuse; a separate `VACUUM`
returns the bytes, and it needs room for a second copy while it runs. Back up first.

---

## 8. A fix was made and nothing changed

**The collector loads its code once, when it starts.** Editing a file, committing it, and pushing
it all change nothing in the running process. It has to be restarted.

This is easy to get wrong because everything *looks* done — tests pass, the commit is in, the file
on disk is correct — while the process from this morning carries on with the old version.

**Check:** compare the process's start time against the file's last-modified time. If the process
is older, it is running the old code.

**Restarting is free outside market hours.** During the day, do it in the middle of the 5-minute
midday gap.

**Then verify against the live system**, not against the tests.

---

## 9. The watchdog itself failed

If the watchdog dies, you lose the thing that tells you about everything else — so it is built not
to.

It reconfigures its output to UTF-8 at startup and routes every message through a fallback that
degrades to plain ASCII rather than crashing. This was BUG-029: on Windows, output sent to a file
or a pipe defaults to an older character set that cannot encode the emoji in its own status line,
so printing the headline killed the process **before** it reached the alerting code. Detection
succeeded, no alert was sent, and the wreckage looked like the watchdog being broken.

**If it prints "The watchdog itself failed: …"**, that message is the whole diagnosis — it is
designed to survive long enough to say so.

**Check it by hand any time:**

```
python scripts/watchdog.py --dry-run
```

It reports what it sees and sends nothing.

---

## Where the answers live

| | |
|---|---|
| `STATUS.md` | Where the project is right now. Read this first in a new session. |
| `docs/OPERATIONS.md` | What normal looks like, and the routine. |
| `docs/DATABASE.md` | What is in the record, and the traps in it. |
| `docs/RUNBOOK_REAUTH.md` | The weekly broker re-authorisation, step by step. |
| `docs/backlog.md` | Every known open problem. |
| `docs/decisions.md` | Why things are the way they are, dated, with the reasoning. |
| `docs/progress_log.md` | What happened in each session. |
