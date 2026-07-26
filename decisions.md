# decisions.md — Engineering Decision Log (ADRs)

Every significant engineering decision, with reasoning, alternatives, tradeoffs, and date.
Newest first. Historical entries (ADR-001…012) were backfilled from `DEV_JOURNAL.md` and
`DOCUMENTATION.md` on 2026-07-25 — dates reflect when the decision was *made*, not when
it was recorded here.

---

## ADR-016 — Entry IV context must be decoupled from `option_rows` before any pruning
**Date:** 2026-07-25 · **Status:** OPEN — blocks M3.2

**Decision:** Do not implement retention pruning (ADR-014) until entry-time IV context is
either (a) carved out of the pruner, or (b) snapshotted into the `trades` table at logging
time. Recommendation: **(b)**.

**Reasoning:** `db.get_entry_iv_context()` reconstructs entry-time term structure for
logged trades by reading **at-strike `option_rows` from historical snapshots**, and
`DOCUMENTATION.md` §11.4 advertises this as working retroactively. Pruning `option_rows`
by expiry date would destroy this for every trade whose expiries have passed — i.e. every
*completed* trade, which is precisely the population the Regime Analysis 2×2 (M6.2) exists
to study. That analysis is the designated mechanism for resolving the §3.1 IV-ratio
hypothesis. `atm_iv_by_expiry` does not substitute: it stores ATM only, while the analysis
explicitly requires IV at the strikes actually traded.

**Alternatives considered:**
- *Carve-out:* never prune rows whose `(expiry_date, strike)` appears in `trades`. Works,
  but leaves the pruner coupled to the journal schema and grows more complex over time.
- *Snapshot at logging time:* copy front/back IV at the traded strikes into `trades` when
  the trade is recorded. Makes the trade record self-contained and lets the pruner stay
  simple and unconditional.
- *Don't prune at all:* rejected — accepts the ~20 GB/yr trajectory (ADR-014).

**Tradeoff:** Option (b) requires a `trades` schema migration and a backfill for the 6
existing trades (which is still possible *today*, because nothing has been pruned yet —
this window closes the moment pruning runs).

---

## ADR-015 — Defer the repo-wide formatter run until after M1
**Date:** 2026-07-25 · **Status:** Accepted

**Decision:** Add `ruff`/`black`/`mypy` configuration in M0, but do **not** run the
formatter across the codebase until the M1 test suite exists.

**Reasoning:** `black` is semantically safe in principle, but reformatting 9,628 lines with
zero automated tests means any accidental change is undetectable. The cost of waiting is
low; the cost of a silent formatting-induced defect in P&L code is not.

**Alternatives:** Format now in one isolated commit (as originally scoped in the audit);
never adopt a formatter.

**Tradeoff:** Style stays inconsistent for one more milestone. Accepted — the ordering
follows the same "tests before change" principle as the whole M0→M1→M2 path.

---

## ADR-014 — Retention policy: prune `option_rows` past expiry, keep `atm_iv_by_expiry` forever
**Date:** 2026-07-25 · **Status:** Accepted (implementation blocked by ADR-016)

**Decision:** Delete `option_rows` for expiries more than N days past expiration. Retain
`atm_iv_by_expiry` indefinitely.

**Reasoning:** Measured growth is ~82 MB per trading day (~20 GB/yr) with no policy at all.
`atm_iv_by_expiry` is only 3.7 MB for a full month — ~0.2% of the database — and powers most
historical charts. Pruning the per-strike detail for expiries that have already settled
should cut steady-state size by roughly an order of magnitude.

**Alternatives:** Archive to a separate `history.db`; downsample old snapshots to hourly;
keep everything and buy disk.

**Tradeoff:** Per-strike granularity for past expiries is lost permanently — which is
exactly what ADR-016 constrains.

---

## ADR-013 — Dashboard: phased, decision deferred to M5.0
**Date:** 2026-07-25 · **Status:** Accepted

**Decision:** Extract and test a framework-agnostic `core/` (M2), wrap it in FastAPI (M4),
then re-evaluate Streamlit against evidence at M5.0. Do not pre-commit to a rewrite.

**Reasoning:** The evidence that Streamlit has been outgrown is strong and comes from the
project's own docs — every §15.4 performance fix was a workaround for the rerun model, and
all four items in §15.9 are deferred limitations rather than solved problems. But a rewrite
now fixes none of the HIGH-severity findings, and with zero tests there would be no way to
verify the new UI computes the same numbers as the old one, in a tool that drives real money.
`core/` extraction is valuable in every possible outcome and cannot be wasted work.

**Alternatives:** Commit to React immediately; stay on Streamlit permanently; move to
Dash/Panel (rejected as a lateral move — still Python-renders-HTML, full rewrite cost for a
partial win).

**Tradeoff:** Chart-reset and interactivity pain persist through M2 and are not fixed until
M5, if ever.

---

## ADR-012 — Access model: local machine, readable from phone/tablet
**Date:** 2026-07-25 · **Status:** Accepted

**Decision:** Single machine, but reachable from mobile over LAN/Tailscale. Not multi-user.

**Reasoning:** Enables remote monitoring while staying consistent with `DOCUMENTATION.md`
§8.4, which lists multi-user/SaaS under "Do Not Build."

**Consequences:** Promotes M4 (FastAPI) from optional to expected; moves Tailscale from M8
into the M3–M4 window; adds a hard requirement that Streamlit binds localhost only and is
never exposed directly to the LAN.

---

## ADR-011 — Delete superseded documents rather than archiving them
**Date:** 2026-07-25 · **Status:** Accepted (user decision, over a recommendation to archive)

**Decision:** Delete `AUDIT_REPORT_2026-06-25.md` and `spx_dashboard_implementation_plan.md`
outright rather than moving them to `docs/archive/`.

**Reasoning (user):** Keep the repository root clean; both are historical and recoverable
from git.

**Recommendation that was overridden:** Archiving was advised because the June audit is
cited as the evidentiary basis for the IV-ratio retraction in `DOCUMENTATION.md` §3.1, §9.4,
the v1.1 changelog, and three `iv_engine.py` docstrings (lines 81-84, 110-111, 360-361).

**Mitigation:** Recovery SHAs recorded — June audit `6329fa28`, implementation plan
`15d1e919` (both reachable from HEAD `bfc78c06`). `backlog.md` DEBT-014 tracks rewriting the
dangling citations during the M2 documentation reconciliation.

**Tradeoff:** ~6 citations now point at files absent from the working tree until DEBT-014
is done.

---

## ADR-010 — Backups use SQLite's online backup API, not file copy
**Date:** 2026-07-25 · **Status:** Accepted

**Decision:** Back up via `sqlite3.Connection.backup()` to a directory **outside** the repo.

**Reasoning:** The collector runs continuously and the database is in WAL mode. A plain file
copy of a live WAL database can capture a torn state. The backup API is transactionally
consistent against an active writer, so backups need no downtime. Storing them outside the
repo means a `.gitignore` regression can never cause a 1.8 GB commit.

**Alternatives:** `VACUUM INTO` (also consistent, but rewrites/compacts and is slower);
file copy with the collector stopped (needs downtime, risks a missed session).

---

## ADR-009 — Fix `.gitignore` corruption at the encoding level
**Date:** 2026-07-25 · **Status:** Accepted

**Decision:** Rewrite `.gitignore` as UTF-8/LF with an explicit warning comment about
PowerShell encoding.

**Reasoning:** The file was UTF-8 with UTF-16LE bytes appended by PowerShell's
`Add-Content`/`Out-File`, which default to UTF-16LE. The intended `pinned_pairs.json` entry
was stored null-separated and matched nothing, so five runtime-state files were tracked for
weeks without anyone noticing. Fixing only the entry would leave the trap for the next
append. The comment documents `Add-Content -Encoding utf8` as the safe form.

---

## ADR-008 — Priority order M0 → M1 → M2, strictly sequential
**Date:** 2026-07-25 · **Status:** Accepted

**Decision:** Cleanup, then tests, then decomposition. No feature work until complete.

**Reasoning:** Refactoring 4,230 lines without tests is how working systems break; building
tests on top of an untracked, unpinned, unbacked repo is building on sand. The ordering is
forced by dependency, not preference.

**Tradeoff:** Feature work and the open v4.1.1 report are deferred.

---

## ADR-007 — Remove the holiday `values` rangebreak from Plotly charts
**Date:** 2026-07-07 · **Status:** Accepted · *(backfilled)*

**Decision:** `_SESSION_RANGEBREAKS` keeps only `bounds=["sat","mon"]` and
`bounds=[16, 9.5], pattern="hour"`. No per-date breaks of any kind.

**Reasoning:** ANY per-date rangebreak corrupts Plotly's point positioning for all data
rendered after it — ghost lines, out-of-order hover, dead tooltips. Both variants
(`values=[dates]` and per-day `bounds=[...]`) were tested and both fail.

**Alternatives:** Keep holiday collapsing and accept corruption (rejected); pre-filter data
server-side (not attempted — the NaN line-breaker in ADR-006 covers the visual need).

**Tradeoff:** Market holidays now render as one session-width of blank space instead of
being collapsed. Accepted: correct rendering beats collapsed holidays. Affects a few days
per year.

---

## ADR-006 — Reinstate `_break_sessions()` NaN line-breaker
**Date:** 2026-07-07 · **Status:** Accepted · *(backfilled)*

**Decision:** Insert a NaN row wherever consecutive points gap by more than 60 minutes.

**Reasoning:** Rangebreaks collapse empty axis *space*; the NaN breaker breaks the *line*
across whatever space remains. Complementary, not redundant — without it, Plotly draws a
straight connector across holidays and outages.

**Known gap:** Not yet wired into the selected-strike IV chart (tracked as DEBT-009).

---

## ADR-005 — Change-triggered fragment poller replaces blind autorefresh
**Date:** 2026-07-03 · **Status:** Accepted · *(backfilled)*

**Decision:** Replace `st_autorefresh` with an `st.fragment(run_every=…)` poller that reruns
only when `snapshot_id` changes.

**Reasoning:** Blind autorefresh forced a full-page rerun every 60–300 s regardless of new
data, resetting Plotly zoom/pan mid-analysis.

**Tradeoff:** When a new snapshot *does* land the rerun is still full-page, so charts still
reset at that moment. True in-place updates need charts inside fragments — deferred.
**This limitation is a primary input to the M5.0 Streamlit re-evaluation.**

**Follow-on defect:** The first implementation caused an infinite rerun loop on fresh
sessions (the compared key was set further down the script); fixed by adopting the latest
snapshot silently on first check.

---

## ADR-004 — `UNIQUE(snapshot_id, expiry_date, strike, right)` + `INSERT OR IGNORE`
**Date:** 2026-07-03 · **Status:** Accepted · *(backfilled)*

**Decision:** Add a unique index on `option_rows`, dedupe once, and switch inserts to
`INSERT OR IGNORE`.

**Reasoning:** `option_rows` had no uniqueness guarantee, so a re-fetch could store the same
contract twice per snapshot. Six-leg `LEFT JOIN` history queries fanned those duplicates
out, rendering as a regular sawtooth and slowing far-OTM pairs.

**Tradeoff (now a known issue):** `INSERT OR IGNORE` also silences *legitimate* write
failures — a genuine constraint violation is discarded identically to a benign duplicate,
with no count and no log line. Tracked as DEBT-008.

---

## ADR-003 — Read-only, non-committing dashboard connections
**Date:** 2026-07-03 · **Status:** Accepted · *(backfilled)*

**Decision:** `_make_conn(read_only=True)` sets `PRAGMA query_only = ON`; `get_conn` does not
commit; the 20 pure-read functions were repointed from `managed_conn` to `get_conn`.

**Reasoning:** Every `SELECT` was running a pointless `conn.commit()` through a write-oriented
context manager. `query_only` makes a dashboard write *physically impossible* rather than
merely discouraged — the reader/writer split becomes enforced, not conventional.

**Bug found en route:** `delete_trade` (a genuine `DELETE`) was wrongly using the read context
manager.

---

## ADR-002 — Custom session-state tab bar instead of `st.tabs()`
**Date:** 2026-06-30 · **Status:** Accepted · *(backfilled)*

**Decision:** Build the six-tab navigation from styled buttons in a keyed container.

**Reasoning:** `st.tabs()` exposes no API to switch the active tab programmatically, which
Mission Control's "→ View Chart" drill-down requires.

**Tradeoff:** Hand-rolled navigation plus the `pending_*` → `*_select` promotion pattern to
work around Streamlit's "cannot modify widget after instantiation" restriction. **Another
input to the M5.0 re-evaluation.**

---

## ADR-001 — Retract the IV-ratio favorability claim; demote to HYPOTHESIS
**Date:** 2026-06-25 · **Status:** Accepted · *(backfilled — the project's most consequential decision)*

**Decision:** Retract the claim that IV ratio < 1.0 is "favorable" and "maximizes
transformation credit." Demote regime favorability to an explicitly unvalidated
`HYPOTHESIS`. Correct the inverted backwardation/contango terminology. De-valence the
dashboard's regime colors.

**Reasoning:** The claim rested on a single paper trade (Category D evidence). Black-Scholes
analysis suggested the *opposite* structural relationship: higher front IV means more
extrinsic value on the short legs, hence more decay to harvest.

**Alternatives:** Keep the original claim (rejected — unsupported); flip to the opposite rule
(**explicitly rejected** — a handful of modeled scenarios with assumed IV paths is not
sufficient to install the reverse as proven either).

**Tradeoff:** The dashboard offers no validated entry signal, only neutral context. Accepted
deliberately: honest uncertainty beats a confident wrong rule.

**Validation mechanism:** the Regime Analysis 2×2 (M6.2), which needs ~20+ trades. Currently
6. **This is the decision that ADR-016 exists to protect.**

> **Standing rule established here:** no claim enters `DOCUMENTATION.md` as fact using words
> like *confirmed / proven / favorable / optimal / maximizes* unless it is mathematically
> derived (with the derivation shown) or backed by a stated minimum sample size.
