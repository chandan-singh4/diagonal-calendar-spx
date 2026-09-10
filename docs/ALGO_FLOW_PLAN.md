# Algo Flow / Net Flow — research note and build plan

**Status:** PLAN ONLY. Nothing in this document has been implemented.
**Raised by:** Chandan, 2026-09-07 (Tradytics "Algo Flow"). Researched 2026-09-07/08.
**Related:** `core/flow.py` (the honest neighbouring metric), ADR-046, BUG-030.

---

## 1. What the indicator is

Every options trade is classified as buyer-initiated or seller-initiated, converted
to a signed premium, summed per minute, and plotted as a running cumulative line
over the underlying price.

    premium     = price x size x 100
    bullish (+) = calls BOUGHT, puts SOLD
    bearish (-) = calls SOLD,   puts BOUGHT
    Algo Flow   = cumulative sum of signed premium, calls and puts combined
    Net Flow    = the same, calls and puts as two separate lines

The whole signal lives in the classification. The arithmetic is trivial once the
buy/sell flag exists.

---

## 2. Why the Schwab record cannot produce it

**This is settled, not an open question.** The header of `core/flow.py` already
reached the same conclusion for gexstream's strike-flow panel.

The collector snapshots the CHAIN — a photograph of the order book. Algo Flow needs
the TAPE — the list of trades that actually happened. `option_rows.volume` is the
exchange's running session total, so between two snapshots we know 250 contracts
traded and nothing else: not how many trades, not their sizes, not their prices,
not the quote standing at each one.

Polling faster does not fix this. It shortens the gaps between photographs; it does
not turn state into events. Schwab has no options time-and-sales endpoint at any
cadence or price.

**Rule for whatever gets built: a fabricated aggressor flag is forbidden.** A guessed
direction produces a chart indistinguishable from the real thing and carrying no
information. Given ADR-046 / BUG-030 / ADR-049, silently-wrong data is this
project's characteristic failure, and this would be the most dangerous instance yet.

---

## 3. Webull supplies the missing piece — verified

Verified 2026-09-08 by downloading the official SDK (`webull-python-sdk-mdata`
0.1.18) and reading the protobuf definitions.

    Tick : ['time', 'price', 'volume', 'side']      <-- side is given

    class Direction(EasyEnum):
        B = 'Buy  (traded at the first set bid price)'
        S = 'Sell (traded at the first set ask price)'
        G = 'Buy  (traded at a higher price than the first set bid price)'
        L = 'Sell (traded at a lower price than the first set ask price)'
        N = 'Neutral'

    Category.US_OPTION = (2, 'US OPTION')           <-- options are first-class

Webull performs the buy/sell classification. We do not need to reconstruct the
bid/ask at trade time.

Endpoints (options added 2026-05-30, under the "Non-Display Solution"):

| Endpoint | Shape | Notes |
|---|---|---|
| `/market-data/options/ticks/list` | time range | **the one to use** |
| `/market-data/tick` (older, gRPC) | last N ticks | count max 1000 — truncation risk |
| `/market-data/options/snapshots/list` | batch | `get_snapshot` takes up to 100 symbols |
| `/market-data/options/bars/...` | historical bars | |

Entitlement: "OPRA Real-Time Non-display for options last sale and quotation".
Chandan already pays ~$33.50/month. **An app/desktop advanced-quotes subscription
does NOT carry over to the OpenAPI** — confirm which one is being paid for.

`get_tick` is SINGLE-SYMBOL. Only snapshots batch. Budget accordingly.

### Open questions — resolve BEFORE building

1. **Is the `side` convention inverted?** The enum text says B = "traded at the bid",
   which is backwards from US convention (a trade at the bid is a SELL). Probably a
   translation artefact. **Validate against a known large sweep.** Getting this
   backwards flips the entire indicator, and it would still look plausible.
2. **How is `N` (Neutral) handled?** Mid-market prints. Needs a stated, documented rule.
3. **History depth of `ticks/list`?** Assume shallow until proven otherwise.
4. **Which rate limit applies?** Webull's page says 300/min; a search suggested
   600/min; Option Snapshot is documented at 60/min. Confirm for `ticks/list`.

---

## 4. Request budget — measured, not estimated

Measured against the real record for 2026-09-04 (128 snapshots, ~3,060 contracts):

- contracts that traded per 5-min bucket: min 32, **median 391**, mean 400, max 882
- contracts that traded at ALL during the day: **2,085 of 3,056** (68%)
- therefore ~87% of contracts do not trade in a given bucket

**The existing Schwab collector is the targeting system.** Diffing
`option_rows.volume` between consecutive snapshots says exactly which contracts
traded, so tick requests are spent only on those. Without it you would blindly
query all 3,000+.

Requests needed per session (one request per traded contract per window):

| Fetch every | Windows/day | Requests/day | Req/min | @60 | @300 | @600 |
|---|---|---|---|---|---|---|
| 1 min  | 127 | 50,841 | 130 | no | OK 43% | OK 22% |
| 5 min  |  82 | 42,093 | 108 | no | OK 36% | OK 18% |
| 15 min |  37 | 26,735 |  69 | no | OK 23% | OK 12% |
| 30 min |  16 | 16,323 |  42 | OK | OK 14% | OK  7% |
| EOD    |   2 |  2,601 |   7 | OK | OK     | OK     |

**Key insight: fetch cadence is NOT chart resolution.** Ticks carry their own
timestamps, so fetching every 5 minutes over a time range still yields a genuine
1-minute line. Wider windows are cheaper AND lossless. This is the opposite of the
Schwab collector, where cadence genuinely bounds resolution.

Burst caveat: the busiest single minute (the open) had 461 contracts trade. At
300/min that minute takes ~1.5 min to drain and you catch up later. At 600/min it fits.

---

## 5. Recommended design

**Leave the Schwab collector exactly as it is.** Its cadence (1-min at the edges,
5-min midday) is correct for its own job, and it doubles as the tick targeting system.

Add a SEPARATE tick fetcher:

- cadence **5 minutes** (a third of 1-min's load, headroom for retries, no resolution cost)
- for each window: diff volume between the bounding snapshots to get the traded list
- call `ticks/list` per contract for that time range
- store raw ticks; compute Algo Flow as a derived view, never in place of the raw record
- accepts a ~5-minute lag. If unacceptable, narrow to 0-7 DTE (690 of the 2,085
  traded contracts, and the bulk of the premium) rather than raising the cadence

**Storage:** a new `option_ticks` table. Do NOT widen `option_rows`. Measure actual
size on day one and revisit retention before it becomes a problem — see the disk
warning below.

**Symbol mapping:** Webull symbols must be joined to our (expiry, strike, right)
key. Capture the OPRA `symbol` field from Schwab (section 6) rather than
reconstructing it, or write a tested reconstruction function. This is the join key;
get it wrong and everything downstream is silently mismatched.

**Validation gate before the chart is trusted:**

- confirm the `side` convention on a known sweep
- reconcile summed tick volume against `option_rows.volume` deltas for the same
  window. They should agree. Where they do not, the tick set is incomplete and the
  bucket must be blank, not zero — the same rule as `core/flow.py`.

---

## 6. Tier 1: Schwab fields we already receive and discard

Schwab sends ~48 fields per contract; `schwab_client.chain_to_dataframe` keeps 11.
No extra API calls, no extra bandwidth — purely a parser change plus columns.

**Capture these:**

| Field | Why |
|---|---|
| `symbol` | OPRA symbol — **the join key to any external tape**. Capture before buying one. |
| `bidSize`, `askSize` | depth behind the quote |
| `lastSize` | size of the most recent print |
| `quoteTimeInLong`, `tradeTimeInLong` | tells a stale contract from an active one — impossible today |
| `rho` | we store four Greeks and drop the fifth for no stated reason |
| `theoreticalOptionValue`, `theoreticalVolatility` | independent cross-check on iv_engine |
| `openPrice`, `highPrice`, `lowPrice`, `closePrice` | per-contract daily range |
| `netChange`, `percentChange` | change since prior close |

**Already derived, do not store:** `mark`, `intrinsicValue`, `extrinsicValue`,
`daysToExpiration`, `strikePrice`, `expirationDate`, `putCall`.
(Note `collector.py:427-437` computes mark and intrinsic by hand while Schwab sends
both in the same payload. Not a bug; just duplicated effort.)

**Skip — measurably not free:** `description`, `exchangeName`, `multiplier`,
`optionRoot`, `exerciseType`, `expirationType`, `lastTradingDay`, `deliverableNote`,
`optionDeliverablesList`, `pennyPilot`, `mini`, `nonStandard`, `inTheMoney`,
`hi52`/`lo52`, `markChange`, `markPercentChange`. Roughly another 90–120 bytes/row;
`description` alone is ~35 bytes repeated 400,000x/day.

### Storage cost — measured

Current: `option_rows` = 19.29M rows / 2.47 GB table + 1.19 GB indexes =
**128 bytes/row**, ~400,000 new rows/day, **~48 MB/day**.

The capture list above adds ~119 bytes/row — roughly DOUBLING growth to ~96 MB/day.

Three optimisations cut that to ~71 bytes/row (~77 MB/day):

1. **Derive `symbol` rather than store it** (−22 B/row) — deterministic from
   root + expiry + right + strike. Needs a tested function.
2. **Store the two timestamps as offsets from `snapshot_timestamp`** (−10 B/row) —
   seconds, not 13-digit ms epochs. Same information.
3. **Move `openPrice`/`closePrice` to a daily table** (−16 B/row) — constant within
   a session, currently stored 126x/day.

Migration mechanics are cheap: SQLite `ALTER TABLE ADD COLUMN` does not rewrite
existing rows, and trailing NULLs are omitted from the record format. The existing
19M rows cost **zero** extra bytes. All growth is future rows.

### !! DISK BLOCKS THIS !!

**20 GB free on C: as of 2026-09-08.** `dashboard.db` is 3.68 GB; a 3.5 GB backup
(`dashboard.db.2026-09-03-pre-bug030`) sits beside it.

- at ~48 MB/day: ~7 months headroom
- at ~96 MB/day: **~3.5 months**

`config.RETENTION_DAYS = 90` but nothing runs it on a schedule (`config.py:104-106`).
Oldest data is 2026-06-23 — 77 days — so the first prune has never run and is due
in about two weeks.

**Sort disk out BEFORE adding columns.** A full disk mid-session means silently
missed collection cycles, which is exactly the failure this codebase's history is
made of.

---

## 7. Underlying minute bars

We never call Schwab's price-history endpoint. It is available and free:

| Granularity | Reaches back |
|---|---|
| 1-minute | **~48 days** |
| 5/10/15/30-minute | ~9 months |
| daily / weekly | to 1985 |

Collection began 2026-06-23, which is 77 days ago — **outside the 1-minute window**.
1-minute reaches back only to ~2026-07-21.

- **Backfill 1-minute NOW and keep doing so** — the window rolls forward daily, so
  every day of delay permanently loses a day.
- For 2026-06-23 to 2026-07-21, 5-minute is the best available. Mixed granularity is
  fine if labelled honestly.

---

## 8. Cost comparison

| | Webull | Theta Data Standard | Massive (ex-Polygon) Advanced | Databento Standard |
|---|---|---|---|---|
| Monthly | **$33.50 (already paid)** | $80 | $199 | $199 + per-GB historical |
| Live tape | yes | yes | yes | yes |
| Side pre-classified | **yes** | no (derive) | no (derive) | no (derive) |
| Historical tape | unknown, assume none | **back to 2016** | 5+ years | deep, billed per GB |

**Decision: use Webull for live capture.** It is already paid for and it hands over
the classification.

If `ticks/list` history proves shallow, consider **one month** of Theta Data Standard
(~$80 once) purely to pull historical ticks for validating the indicator, then cancel.
Otherwise the indicator ships unvalidated and you wait months to learn whether the
cumulative line actually leads price or merely looks like it does.

---

## 9. Build order

1. Confirm the $33.50 is the **OpenAPI** OPRA entitlement, not the app-side one.
2. One `ticks/list` call on one SPX contract for a past date — answers history depth
   AND the `side` convention in a single request.
3. Confirm the rate limit for `ticks/list` (60 / 300 / 600).
4. Tier 1 Schwab field capture, including `symbol` (needs disk sorted first).
5. Backfill 1-minute SPX bars before the 48-day window rolls further.
6. `option_ticks` table + 5-minute tick fetcher driven by volume-diff targeting.
7. Validation gate (side convention; tick volume vs `option_rows.volume` deltas).
8. Only then: the Algo Flow / Net Flow chart.

The Webull MCP connector is listed for the Claude session but NOT authorised.
Authorising it via claude.ai connector settings would let steps 1–3 be answered
directly.

---

## Appendix: pre-market open interest (measured 2026-09-08 07:59 ET)

A live chain fetch before the open returned:

- 3,840 contracts, **open interest populated on all of them** (2,425,038 total)
- **volume = 0 on every contract** — confirming the session counters had reset and
  the OI figure is the settled overnight number, not a mid-session one
- bid populated on all 3,840; IV on 3,789

Compared against the last stored snapshot (6387, 2026-09-04 20:01) over the 3,020
overlapping contracts: **70% had a different OI**.

So OI is available well before 09:30 and the collector's hard 09:30 gate
(`core/session.py:33`) means the first stored OI of the day is later than it needs
to be. A single pre-open poll (~09:00 ET) would capture settled prior-session OI
before any of it is mixed with the new session. Worth a small, separate change —
NOT a change to the main collection cadence.
