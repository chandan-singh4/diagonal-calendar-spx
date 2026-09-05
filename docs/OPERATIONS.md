# OPERATIONS.md — running it

**Almost all of this is "do nothing".** The collector starts itself, sleeps outside market hours,
wakes at 9:30, and stops at 16:02. There is exactly **one recurring chore** — the weekly broker
re-authorisation — and something will tell you when it is due.

When something is wrong, go to `TROUBLESHOOTING.md`. This file is what normal looks like.

---

## The one thing you have to do: re-authorise Schwab, weekly

The broker's permission lasts **7 days** and cannot be extended — that is Schwab's rule, and the
browser login is a deliberate security boundary, so it can never be made automatic.

```
python scripts/reauth.py
```

**Three independent things tell you it is due**: a banner in the dashboard from day 6, the
watchdog's pop-up and email, and `python scripts/reauth.py --check`.

Full steps, failure modes, and the one trap to avoid are in **`RUNBOOK_REAUTH.md`**. The short
version of the trap: `get_client()` does *not* start a login if the old token file still exists —
it quietly loads the stale token, prints nothing, and you find out on Monday. Use the script.

---

## What runs by itself

**The collector** starts at logon from a shortcut in your Startup folder
(`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\SPX Diagonal Collector.lnk`), created
22 June. It sleeps outside market hours and wakes on its own.

> Note: `scripts/register_collector_task.ps1` sets up a Task Scheduler entry instead. **It is not
> what runs today** and the task does not exist — see DEBT-040. Do not assume from the presence of
> that script that the collector is a scheduled task; it is not.

**The watchdog** *is* a scheduled task (`SPX Collector Watchdog`), registered by
`scripts/register_watchdog_task.ps1`. It answers one question from outside the dashboard — are
prices still arriving? — and speaks through a desktop pop-up and an email. It exists because on
9 August the token had expired and the dashboard's red banner was working perfectly with nobody
looking at it.

**It watches; it never acts** (ADR-045). It will not restart the collector, delete anything, or
change any setting. It tells you, and you decide.

Its alarm path is proven on a real outage: 19 August, four failed cycles from 12:30 ET, alert
eight minutes later, and a recovery notice afterwards.

---

## The collection day

| | |
|---|---|
| 09:30–10:00 | polls every **60 s** — the open is volatile and worth the resolution |
| 10:00–15:30 | polls every **300 s** |
| 15:30–16:02 | polls every **60 s** |

**128 snapshots** on a full day, about **82 MB**.

**It runs to 16:02, not 16:00, on purpose** (ADR-049). SPX is a cash index struck from its
components' closing auction prints, and those arrive over the seconds *after* the bell. A poll at
16:00 records a "close" that is not the close — which is worse than recording nothing, because it
looks right. For ten weeks the window ended at 16:00 exclusive, the last poll of every day landed
at 15:59:5x, and every closing price in the record was wrong by a couple of points.

It stops at 16:02 rather than 16:15, where the options actually stop trading, because SPX itself
freezes at 16:00 — volatilities computed after that use a stale underlying while option marks keep
moving.

---

## Checking on it

**Is it collecting?** — the quick look, safe any time:

```
python scripts/check_db.py
```

Snapshots today and all-time, the last five with their key fields, the IV term structure from the
most recent one, and any recorded gaps.

**Is the record actually complete?** — the deeper question:

```
python scripts/audit.py           # the whole history, cheap checks
python scripts/audit.py --deep    # also scans the 18.8M-row table
```

This is **not** a test. Tests run against a temporary database and prove the *code* behaves; the
audit reads the real record and asks whether what you have is *there*. Every test in the suite
passed throughout three separate cases of data never being captured, because they were written
against what the code was believed to do.

It is **read-only by construction** — SQLite refuses a write on its connection — and it repairs
nothing, deliberately. Several of its findings are expected history, telling them apart needs a
person, and an audit that edited the one irreplaceable file would be a second thing that can go
wrong.

**Read its severities properly.** `alarm` needs attention. `note` is known history it is listing
so the counts reconcile — a short day the collector already recorded a gap for is a note, not a
finding. An audit that cries wolf gets skimmed within a week.

**Worth running the morning after any change to collection**, and specifically after 09:30, since
that is when the awkward data arrives.

---

## Occasional housekeeping

**Back up before anything structural.** About 2.5 minutes for 3.5 GB; see `DATABASE.md`. Then open
the copy and check it — a file of the right size proves nothing.

**Prune old per-strike detail** when disk gets tight:

```
python scripts/prune.py             # reports. Deletes nothing.
python scripts/prune.py --execute   # deletes, after you type the row count
```

`--execute` stops and makes you type the number of rows, not a y/n. It also refuses to run
without a recent backup unless you say `--no-backup-check` in as many words. Option rows are
prunable 90 days after their expiry; summaries, snapshots and gaps are kept forever; any expiry a
real trade used is exempt at any age (ADR-044).

**Reclaim the space afterwards.** Deleting rows does not shrink the file — SQLite keeps the pages
for reuse. A separate `VACUUM` is what returns the bytes to the disk, and it needs room for a
second copy of the database while it runs. The 0.10 pass reclaimed 387 MB this way.

**Disk headroom:** the record grows ~82 MB per trading day, roughly **20 GB a year** unmanaged.

---

## Changing the code while it is running

**The collector loads its code once, at startup.** Editing a file changes nothing until it is
restarted — a fix committed at 2 p.m. is still not running at 4 p.m. unless you restarted it.

**Restarting costs nothing if you time it.** Outside market hours it is free. During the day, do
it in the middle of the 5-minute gap: a restart at 12:12, 59 seconds after a poll, produced its
next snapshot 83 seconds later and recorded no gap at all.

**Then verify on the real system.** Tests passing is not evidence that the running process changed.

---

## The API, and reading it from a phone (M4)

A second server, separate from the dashboard, that serves the record as data
rather than as a page. **The dashboard does not need it and is unaffected by
it** — start it, or do not, and nothing about the screen changes.

    python -m uvicorn api.app:app --host 127.0.0.1 --port 8899

`GET /health` says whether it can read the record and how old the newest
snapshot is. **It does not go red when the collector is quiet**, because that
is the correct state every evening and all weekend; judging silence needs the
market calendar and is the watchdog's job (ADR-045). Interactive documentation
for every endpoint is at `/docs` once it is running.

**Both servers bind 127.0.0.1 only.** For Streamlit this is set in
`.streamlit/config.toml` and closes OPS-006 — before 2026-09-05 `streamlit run`
advertised a Network URL and an External URL, so the dashboard was reachable
from anything on the Wi-Fi. For the API it is the `--host` argument above, and
leaving it off would undo this.

### Reaching it from the phone

Tailscale, not port forwarding. Tailscale connects to the loopback interface,
so localhost-only binding costs nothing and keeps the LAN out.

**Set a token first.** In `.env`:

    SPX_API_TOKEN=<a long random string>

With no token set the API is open, which is right for a server only this
machine can reach and wrong the moment anything else can. Clients send it as
the `X-API-Token` header. `/health` stays reachable without it so a monitor
can check the server is up without holding the secret.

**What the token does not do:** there is no rate limiting, no audit log, and
revoking means changing the value and restarting. It is not enough to make the
API safe on the open internet — the tunnel is the exposure model, and the
token is what stops the tunnel being the only thing in the way.

### Live updates

`ws://127.0.0.1:8899/ws/snapshot` pushes a message when a snapshot completes,
so a client does not have to keep asking. The first message on connect is
labelled `current` and reports where things stand; later ones are labelled
`snapshot` and mean something has just changed. **A restart announces
nothing** — whatever is already recorded is the baseline, not news.

---

## The routine, in one place

| When | What |
|---|---|
| Every ~7 days | `python scripts/reauth.py` — the only unavoidable chore |
| After changing collection | Restart the collector, then check a live poll |
| Morning after any change | `python scripts/audit.py`, after 09:30 |
| Now and then | `python scripts/check_db.py` |
| Before anything structural | Back up, and verify the backup |
| When disk gets tight | `prune.py`, then `VACUUM` |
| Never | Delete rows by hand. Trust a warning you have seen every day. |
