# Development Journal — SPX Diagonal Calendar Analyzer

Log every change here. Format:

```
## YYYY-MM-DD — Short title
**Changed:** what you did
**Why:** the reason / problem it solves
**Impact:** effect on strategy logic or dashboard behavior
**Open questions / follow-ups:** anything left unresolved
```

Newest entries at the top.

---

## 2026-06-21 — Feature: strike-specific IV chart + independent expiry selectors
**Changed:**
- `db.py`: Added `strike_snapshots` table (expiry, strike, side, iv, bid, ask, volume, OI)
  with `save_strike_snapshot()` and `get_strike_history()` functions. ATM snapshots
  (existing) and strike snapshots are written independently each poll.
- `iv_engine.py`: Added `StrikeContract` dataclass and `strike_contract()` function
  — looks up a specific strike/side/expiry in the live chain_df, falls back to
  nearest available strike with a `found_exact=False` flag if the typed strike
  doesn't exist in the chain (e.g. coarser spacing far from spot).
- `app.py`: Full layout restructure:
  - Left panel: expiry selectors (now with stable `key=` — truly independent),
    + new Strike Selection section with two `st.number_input` fields (call strike,
    put strike), free-typed, defaulting to ATM and ATM-100 as starting suggestions.
    Live contract data (Front IV / Back IV / Ratio) shown per strike beneath inputs.
  - Right panel: TWO stacked charts:
    - TOP — "Selected-Strike IV": front vs back for the typed call strike (solid lines)
      and put strike (dotted lines), plus their respective IV ratios. Only renders
      once strike-specific history exists (first poll after strikes are entered).
    - BOTTOM — "ATM IV": existing floating-ATM chart, now explicitly labeled as
      "macro context."
  - All four legs recorded each poll when strikes are set: front_call, front_put,
    back_call, back_put (same strike both expiries, per confirmed strategy).

**Why:** ATM-only IV was confirmed insufficient for actual entry decisions — the
IV at your specific strike is what determines the real edge of a given trade, not
the floating ATM proxy. Also resolved the front/back expiry coupling bug (missing
`key=`) and the "only-selected-expiries-recorded" history gap from the prior session.

**Design decision logged:** User confirmed diagonal structure uses same strike on
both front and back for a given side (not four independent strikes). Transformation
to IC/double-diagonal handled separately via limit orders, not modeled in the IV
dashboard — IV analysis is entry-only.

**Impact:** Strike-specific chart will be blank until a few data points accumulate
(per the ~10s poll interval). ATM chart behavior unchanged.

**Open questions / follow-ups:**
- Demo mode's synthetic chain data uses a basic smile model (IV rises away from ATM)
  so the strike-specific chart will work in demo mode too, but IV values won't
  reflect realistic skew for a specific SPX strike — that's fine for UI testing.
- Theta Advantage score component still placeholder at 50. Next phase item.

## 2026-06-21 — Fix: front/back expiry coupling, and only-2-expiries-recorded history gap
**Changed:**
- `app.py` expiry selectors: removed the `back_options = [e for e in available_expiries if e > front_expiry]` filtering and the lack of `key=` on both selectboxes. Both now use the full expiry list with stable `key="front_expiry_select"` / `key="back_expiry_select"`, plus a non-blocking warning if Back ends up earlier than or equal to Front.
- `app.py` snapshot saving: now loops over every expiry visible in `chain_df` each poll and records its ATM IV, instead of only recording the two currently-selected (front/back) expiries.

**Why:** (1) Without explicit `key=`s, Streamlit treated the Back selectbox as a brand-new widget every time its option list changed (which happened every time Front changed, since the list was filtered relative to Front) — so it always reset to `index=0` of the new filtered list, which looked like "Back auto-snaps to a fixed offset after Front." (2) Only ever recording IV for the actively-selected pair meant switching the Front/Back dropdowns reset visible history to zero for the newly-selected pair — compounding the (separate, expected) issue of having started live data collection only today.

**Impact:** Front and Back are now fully independent selections that persist across reruns. History now accumulates for every visible expiry regardless of which pair is on screen, so switching expiries won't lose data going forward. This does NOT retroactively create history for today before this fix — only data captured after this change is recorded per-expiry-broadly.

**Open questions / follow-ups:** Strike-specific IV (not just ATM) is the next real gap, per feedback from another chat in this project — ATM-only IV doesn't reflect the actual contracts once specific strikes are selected for a trade. Scoping that as the next feature: needs a strike selector UI, a schema extension to store per-strike IV history, and `iv_engine` functions for non-ATM strike lookup.

## 2026-06-21 — Fix: OAuth "Redirect server exited" — switch to manual flow
**Changed:** `schwab_client.py`'s `get_client()` — replaced `schwab.auth.easy_client()`
with a manual check: load the cached token via `client_from_token_file()` if one
exists, otherwise authenticate via `schwab.auth.client_from_manual_flow()`.
**Why:** Your registered Schwab callback URL is `https://127.0.0.1` (no port).
`easy_client`'s automatic flow (`client_from_login_flow` under the hood) requires
a port number so it can spin up a local listener to auto-capture the OAuth
redirect — without one it fails with "Redirect server exited." The fix would be
adding a port to your registered callback URL, but per `schwab-py`'s own docs,
changing a registered callback URL likely triggers Schwab re-approval (can take
days). `client_from_manual_flow()` avoids the local listener entirely — it has
you copy-paste the post-login redirect URL by hand — so it works with your
exact already-approved callback URL, no portal changes, no re-approval wait.
**Impact:** First-ever login now requires one manual copy-paste step in the
terminal instead of being fully automatic. Every login after that (i.e., once
`data/token.json` exists) is unaffected — same automatic token-file loading and
refresh as before.
**Open questions / follow-ups:** This is still untested against the real Schwab
OAuth endpoint from my side (sandbox network can't reach it) — next checkpoint
is whether the manual copy-paste flow completes successfully and `token.json`
gets created.

## 2026-06-21 — Fix: Demo Mode silently reverting after process restart
**Changed:** `config.py` — `DEMO_MODE` now defaults to OFF automatically once real
`SCHWAB_APP_KEY`/`SCHWAB_APP_SECRET` are present in `.env`, instead of always
defaulting to ON. An explicit `DEMO_MODE=` value in `.env` still overrides this
either way.
**Why:** Confirmed root cause of "nothing happens when I toggle Demo Mode off and
restart" — every Streamlit process restart resets widget state to its coded
default, and that default was hardcoded to `True`. So toggling Demo Mode off in
the browser, then restarting the process (e.g. to pick up new `.env` values, per
the normal setup flow), silently flipped it back to Demo Mode without any error —
confirmed by `demo_dashboard.db`'s modified timestamp continuing to update every
poll cycle while `dashboard.db`/`token.json` never got created.
**Impact:** First-time setup still defaults to Demo Mode with zero config (no
credentials = no reason to default to live). Once real credentials exist, a
process restart no longer silently reverts to synthetic data — closes the exact
gap that caused the confusion.
**Open questions / follow-ups:** Live OAuth flow itself still untested from my
side (sandbox can't reach Schwab) — next real checkpoint is whether the login
popup/error appears correctly on the next restart.

## 2026-06-21 — Fix: mixed-precision timestamp parsing crash
**Changed:** `app.py` line ~138 — `pd.to_datetime(merged["timestamp"])` now passes
`format="ISO8601"`.
**Why:** Found while running locally for the first time. Seeded demo timestamps and
live-saved timestamps had inconsistent precision — Python's `datetime.isoformat()`
omits the microseconds segment when it's exactly zero, so some stored rows looked
like `...T14:30:00+00:00` and others like `...T19:26:22.672015+00:00`. Pandas
inferred one rigid format from the data and crashed (`ValueError: time data ...
doesn't match format`) the first time a mixed batch got parsed.
**Impact:** Chart now renders correctly. No data was lost — this was a parsing
bug, not a storage bug.
**Open questions / follow-ups:** None — confirmed working after the fix.

## 2026-06-21 — Flux-style chart, demo mode, schema redesign
**Changed:**
- Rebuilt the dashboard layout to match the NavigationTrading FLUX reference (top metric strip: IV Ratio/Front IV%/Back IV%/IV Index; expirations panel with day-change; dual-axis Front/Back IV + Ratio chart; historical range bars).
- Added a time-range selector: Today / 5D / 10D / 15D / 1M (per your request, beyond Flux's Today/5D/20D).
- Redesigned the DB schema from "snapshot per front/back pair" to "snapshot per expiry," so changing the front/back dropdown doesn't require fresh history — any two expiries' stored history can be joined on timestamp after the fact.
- Added `demo_data.py`: a synthetic, mean-reverting IV generator and a fake option chain, writing to a separate `demo_dashboard.db` so it never touches real collected data. Added a "Demo Mode" toggle in the sidebar (`app.py`), on by default, so the dashboard is runnable today with zero Schwab credentials.
- Added `iv_engine.range_stats()` for the historical-statistics range bars.

**Why:** You wanted the FLUX-style visualization and to "build something today" — but this sandbox can't reach `api.schwabapi.com` (network allowlist), so live data has to come from your machine. Demo Mode closes that gap: same UI, same chart code path, synthetic data instead of live, so you can verify the dashboard works *today* and just flip a switch once your local Schwab auth is wired up.

**Impact:** Breaking schema change — `iv_snapshots` table replaced by `expiry_snapshots`. No real data existed yet, so no migration needed. `app.py`'s data-fetch branch is now demo/live conditional; downstream chart and stats code is identical either way, which is intentional — verifies the same code path you'll actually trade against.

**Open questions / follow-ups:**
- IV Index metric is a simple average-of-all-expiries approximation, not FLUX's proprietary calc — revisit if you want it to mean something more specific.
- Theta Advantage in the Trade Quality Score is still a placeholder (50 flat) — Phase 3 item.
- Smoke-tested the full demo data → chart pipeline in the sandbox (seed → fetch → merge → range stats) — all passed. Have NOT been able to test live Schwab auth/data calls from this environment; that first live run is on you, locally.

## 2026-06-21 — Initial scaffold
**Changed:** Created project structure: README, config, Schwab auth client, SQLite
storage layer, IV term-structure engine, Streamlit MVP dashboard.
**Why:** Establish the baseline architecture before connecting real credentials —
get the plumbing right (auth, storage schema, IV math) before layering on UI polish.
**Impact:** No live strategy logic yet — this is infrastructure only. Trade Quality
Score, transformation calculator, and payoff diagrams are stubbed for Phase 3-5.
**Open questions / follow-ups:**
- Need real Schwab app approval before any live data flows.
- IV percentile calculations will be unreliable until several weeks of history accumulate.
- Decide on polling interval (currently defaulted to 10s) once you've observed actual
  Schwab API rate limit behavior in practice.
