# DATABASE.md — what is in the record, and what will bite you

**The database is the product.** The dashboard can be rewritten in a weekend; the prices cannot
be re-fetched at any price, because no broker sells you last Tuesday's option chain. Everything
in this file exists to keep that one file correct.

**Where it lives:** `data/dashboard.db` · **3.57 GB** · one SQLite file, plus `-wal` and `-shm`
sidecar files while the collector is running (those are normal — do not delete them).

**Span:** 23 June 2026 to today · **6,259 snapshots** · **18.88 million option rows** · growing
about **82 MB per trading day**.

For the *conceptual* data model — what a diagonal is, what the columns mean for trading — see
`DOCUMENTATION.md` section 7. This file is the physical record: what is stored, what is kept,
and the specific ways it has been got wrong.

---

## The six tables

| Table | Rows | What it is | Kept |
|---|---:|---|---|
| `snapshots` | 6,259 | One row per poll. Time, market session, SPX and VIX levels, status, how many rows were stored. | **Forever** |
| `option_rows` | 18,882,219 | The bulk. One row per contract per poll — strike, expiry, bid, ask, greeks. | Prunable (below) |
| `atm_iv_by_expiry` | 126,238 | The at-the-money volatility per expiry per poll — the summary the analytics actually read. | **Forever** |
| `collection_gaps` | 48 | Every stretch of market hours where nothing was collected, and why. | **Forever** |
| `trades` | 6 | The trade journal. Currently six practice entries. | **Forever** |
| `schema_version` | one per migration | Which shape the database is in, and when each change was applied. | **Forever** |

`snapshots` is the spine: everything else points at a `snapshot_id`, with
`ON DELETE CASCADE`, so deleting a snapshot deletes its option rows and its IV summary with it.

---

## Retention — what may be deleted, and what never may

Set by **ADR-044**, and it is deliberately conservative.

- **`option_rows` may be pruned 90 days after the contract's expiry date** (`RETENTION_DAYS` in
  `config.py`). By then the contract has been dead for three months and the per-strike detail has
  no remaining use.
- **Everything else is kept forever.** Snapshots, the IV summaries, the gap log, the journal.
  These are small, and they are what long-run analysis reads.
- **Any expiry a real trade actually used is exempt at any age.** You cannot analyse your own
  fills against prices that were deleted.
- **Nothing prunes on a schedule.** There is no timer, no cron, no automatic cleanup anywhere in
  this project. It happens only when you run `scripts/prune.py` by hand, and that script reports
  by default and needs `--execute` plus a typed confirmation to delete anything.

That last point is a decision, not an oversight. An automatic deleter that gets its date
arithmetic wrong at 3 a.m. destroys the one irreplaceable thing here.

---

## The two unique indexes, and why they are shaped that way

**`uq_option_rows_contract_settle`** — `(snapshot_id, expiry_date, strike, right, COALESCE(settlement, '?'))`

The `COALESCE(settlement, '?')` is the whole story of **ADR-046**. SPX lists **two different
options for each third Friday**: the traditional monthly, which settles against Friday's *opening*
print and stops trading the evening before, and the SPXW weekly, which trades all day and settles
at the close. Schwab returns both under one expiry key.

For eight weeks the index had no room for the difference, so the second contract collided with the
first and `INSERT OR IGNORE` silently dropped it — 160 rows a cycle, 2,181 times. The `settlement`
column ('AM' / 'PM') now distinguishes them.

**A NULL `settlement` means "this row predates 19 August and nobody recorded which contract it
was".** It does not mean "morning". Rows can be attributed after the fact — before expiry day the
stored row is the a.m. contract, on expiry day it is the p.m. one — but that is a **read-time**
judgement, and the stored NULL stays honestly blank.

**`uq_atm_iv_contract`** on `atm_iv_by_expiry` — same idea, and here the insert is a plain
`INSERT`, not `OR IGNORE`, precisely so a collision raises instead of vanishing.

---

## Traps in the columns

These are all real, all found the hard way, and all still true.

**`iv` is a decimal here and a percentage at the broker.** `0.18` in this table means 18%. Schwab
sends `18.4`, and `collector.py` divides by 100 on the way in — deliberately, so `app.py`'s older
readers were unaffected. `schwab_client.py` does *not* do the division, and a conversion drifting
into that layer would be applied twice with nothing raising.

**The greeks are not divided.** `delta`, `gamma`, `theta`, `vega` are stored exactly as sent. This
asymmetry is why BUG-030 looked like two different numbers: the broker's no-value marker read
`-9.99` in the IV column and `-999.0` in the greeks.

**`-999.0` is Schwab saying "I have no value for this."** It is not a price. It arrives mostly at
the 09:30 poll on longer-dated expiries, which have not traded yet when the bell rings. It was
stored verbatim for ten weeks — 5,127 rows — and is now blanked at the parser
(`schwab_client._value_or_none`) and repaired in the record. **The comparison is exact equality**,
because `-9.99` is also a perfectly ordinary theta and 38 rows legitimately hold it.

**A blank means "not recorded", never "zero".** This is the standing rule of the whole project. A
missing price must be NULL. A `0.0` delta on a far out-of-the-money option is a *fact*; a blank is
the absence of one. Anything that conflates them is a bug.

**`mark`, `intrinsic_value` and `time_value` are stored, not derived on read.** They were computed
at collection time from the numbers as they stood then, and recomputing them later against a
different underlying would silently produce different answers.

**`dte` is stored too**, for the same reason: days-to-expiry *at the moment of collection* is a
historical fact, not something to recompute from today's date.

---

## Reading it safely

Open it read-only. Always, for anything analytical:

```python
sqlite3.connect(f"file:{path}?mode=ro", uri=True)
```

This is not a convention to remember — SQLite itself then refuses a write, so a mistake raises
instead of editing the record. `scripts/audit.py` is built this way and has a test that proves a
`delete` against its connection fails.

The dashboard's own reader path (`db._make_conn(read_only=True)`) also sets `PRAGMA query_only`,
so it can never take a write lock or contend with the collector.

**The database is in WAL mode**, which means readers never block the writer and the writer never
blocks readers. You can query it freely while collection is running.

**But a long write blocks the collector.** `db.py` gives it a 15-second timeout. A single
`UPDATE ... WHERE <unindexed column> = ...` scans 18.8 million rows and holds the write lock for
about 50 seconds — long enough to fail a live poll and punch a hole in the record. When a bulk
repair is genuinely needed, scan read-only for the row ids first and then write by primary key.
That is what `scripts/repair_bug030.py` does, and it commits in 0.1 seconds.

---

## Backing it up

`VACUUM INTO` produces a consistent copy while the collector is running, and compacts it:

```python
sqlite3.connect(f"file:{db}?mode=ro", uri=True).execute("vacuum into ?", (dest,))
```

Takes about 2.5 minutes for 3.5 GB. **Then open the copy and check it** —
`PRAGMA quick_check` and a row count — because a file of roughly the right size is not evidence
of anything. Dated backups (`dashboard.db.YYYY-MM-DD-reason`) are git-ignored by convention.

Do not copy the `.db` file with Explorer or `cp` while the collector is running: the WAL sidecar
holds recent writes and a plain file copy will miss them.

---

## Changing its shape

**`schema.py` holds a numbered list of migrations and the runner that applies them** (ADR-051).
To change the schema, add a `Migration` to the end of `MIGRATIONS` with the next number and a
description. That is the whole procedure.

- **Forward-only.** There is no `down()`. Reversing a change to this file means restoring the
  backup you took before making it — which is a real answer, unlike an undo script written when
  nobody was looking at it.
- **It records what it did.** One row per migration in `schema_version`, with a timestamp and a
  description, written in the *same transaction* as the change, so the version and the shape can
  never disagree.
- **It fails loudly.** A migration that raises is rolled back; the database is never left
  half-changed, and earlier migrations stay applied.
- **Old code will not open a newer database.** It raises rather than writing rows in a shape it
  does not understand.
- **`SCHEMA_VERSION` is derived from the list**, never written down beside it.

Use `schema.add_column(conn, table, column, decl)` rather than a bare `ALTER`. It asks
`PRAGMA table_info` first, so "already there" is *known* while a full disk, a locked database or a
misspelled type still raise. The pattern it replaced — `try: ALTER ... except Exception: pass` —
treated all four as success, and left the live database at version 1 after ten changes.

Migrations 2 and 3 check whether their columns exist before adding them. That is a one-time debt,
not the pattern: existing databases already had those columns while still stamped v1, so the two
states are indistinguishable from the version. **Migrations from 4 onward may assume the state
their predecessors left.**

One-off structural repairs that are not schema changes — deduplicating rows, reclassifying values
— live in `migrations/` as standalone scripts with a dry-run default. Those are a different thing
from a schema version and stay separate.

**A `CHECK` constraint cannot be altered in place.** `snapshots.market_session` allows exactly
`'OPEN'`, `'MIDDAY'`, `'CLOSE'`; adding a fourth value would mean rebuilding a table inside a
3.5 GB file. When ADR-049 needed to mark the two polls after the bell, it used the timestamp that
was already there instead — the cheapest correct answer is usually not a schema change.
