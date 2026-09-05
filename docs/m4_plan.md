# M4 — Backend API

**Status:** proposed, not started · **Branch:** `m4-backend-api` · **Drafted:** 2026-09-05

M4 builds a stable, documented HTTP contract over `core/` and `dataaccess/`. Streamlit keeps
running unchanged throughout. It is the gate for M5 (ADR-012 promoted it from optional to
expected, because reading the dashboard from a phone needs a server that is not the page).

---

## What M4 is NOT

**It is not a rewrite of the dashboard, and it does not touch the collector.** If a change to
`app.py`, `views/` or `collector.py` looks necessary to finish a task here, that is the signal
to stop and re-scope — M4's exit condition is that the Streamlit app still runs, byte-identical
in behaviour, next to a new server that reads the same database.

It is also not a public API. ADR-012 settled the access model: **one machine, one user**,
reachable from a phone over LAN/Tailscale. Multi-user and SaaS are on the "Do Not Build" list.
That decision shapes every task below — most obviously, it is why authentication is a single
shared token rather than user accounts.

---

## What M2 already got right

The awkward part of this milestone was paid for in M2, and the ground is better than the plan
assumes:

- **`dataaccess/queries.py` is already the API's data layer.** Ten reads, none of them import
  streamlit, every one takes `db_path` as its first argument — both facts enforced by
  `tests/test_layering.py`, not merely intended. A FastAPI process can call them directly on
  day one.
- **`core/` is pure.** No database, no page, no files, function-of-its-arguments throughout.
- **`db.py` is the only writer.** The API is read-only, so it never goes near that path.

So the API does not need a new data layer. It needs a new *binding* layer — the thing
`services/` is for Streamlit.

## The one hard part: `services/` does not transfer

`services/` is the only layer allowed to import both `config` and `streamlit`, and every
module in it does. Three things live there that the API must solve differently rather than
reuse:

1. **The memo.** `services/loaders.py` is thirteen `@st.cache_data` wrappers and nothing else.
   The API needs its own cache; `st.cache_data` outside a Streamlit script context is not an
   option.
2. **Mission Control's "New" diff.** `_run_mission_control` reads and writes `st.session_state`
   to work out which opportunities are new since the last snapshot. **This is per-session state
   in a single-user app** — and it is genuinely stateful, not incidental. It cannot live inside
   the cached function (the module comment says so explicitly, and it is right). This is the
   only piece of M4 with a real design question in it, which is why it is its own task.
3. **`config.DB_PATH`.** `services/` decides the database. The API layer must decide it too,
   and `dataaccess/` must stay forbidden from reaching for it.

**A cache-key improvement falls out for free.** The Streamlit memo is keyed on `snapshot_id`
but *invalidated by TTL* — 55s, 120s, 300s — so a request landing after a lapse redoes real
work even though the data has not changed. ENH-011 already calls this out: TTL is the wrong
key here, because the data changes on a new snapshot, not on a clock. **The API cache should
be keyed on `snapshot_id` alone with no TTL**, which is both simpler and strictly more correct.
It does not fix ENH-011 (that is Streamlit's own memo, untouched by M4) but it stops M4
inheriting the same mistake.

---

## Tasks

| # | Task | Size | Delivers |
|---|---|---|---|
| **4.1** | `api/` package skeleton, layering rules, health endpoint | S | `GET /health`, the layering test extended, FastAPI declared as a dependency |
| **4.2** | Read endpoints over `dataaccess/` + snapshot-keyed cache | M | The ten reads exposed; the cache with `snapshot_id` as its only key |
| **4.3** | Computed endpoints over `core/` — scanner, mission control, GEX | M | The screen's real answers, not just its rows |
| **4.4** | WebSocket push on new snapshot (ENH-002) | M | `/ws/snapshot`; replaces polling for clients that want it |
| **4.5** | Auth, binding, and the Tailscale runbook | L | Shared-token auth, documented exposure model, `OPERATIONS.md` section |

### 4.1 — Skeleton and rules (S)

A new top-level `api/` package. The layering test gets a new entry:

    api/ may import fastapi, pydantic, core, dataaccess, config, db, pandas.
    api/ may NOT import streamlit, views, ui, services, app.

That last clause is the load-bearing one. **The moment `api/` imports `services/`, it has
imported Streamlit** and the whole point is gone. This is asserted against the source, in the
same file and the same style as the existing rules, rather than trusted to review — that class
of fault is exactly what `test_layering.py` was built for.

FastAPI becomes a declared runtime dependency. Note `starlette`, `uvicorn`, `httpx` and
`anyio` are **already installed transitively via streamlit** but are not declared; 4.1 declares
what it actually uses rather than relying on that accident.

### 4.2 — Read endpoints (M)

One endpoint per `dataaccess/queries.py` function, plus the cache. The cache is keyed on
`snapshot_id` with no TTL, per the reasoning above. Responses are JSON, not DataFrames — which
means a serialisation decision per read, and the existing rule applies: **missing price is
blank, never 0.** `tests/test_query_timestamps.py` already guards timestamp conversion for
display; the API needs the equivalent for the wire, and should reuse that test's shape.

### 4.3 — Computed endpoints (M)

Scanner, Mission Control and GEX. **Mission Control's "New" diff is the open question here.**
Options, to be decided before the task starts rather than during it:

- **(a) Drop "New" from the API** and let it stay a Streamlit-only feature. Cheapest, honest,
  loses something the phone client would plausibly want.
- **(b) Move the diff into the database** — a small table of eligible keys per snapshot. Makes
  it correct for any number of clients and survives a restart, at the cost of a schema
  migration and a write path in a milestone that is otherwise read-only.
- **(c) Keep it server-side in memory**, one "last seen" set per API process. Simple, works for
  a single user, silently wrong if the process restarts mid-session.

**Recommendation: (b), but only if the "New" flag has actually earned its place on the phone.**
Otherwise (a). This is Chandan's call and it is worth asking before writing code, because (b)
turns M4 into a milestone that writes to the database, and that changes what "read-only" means
for the rest of the plan.

### 4.4 — WebSocket push (ENH-002, M)

Push on new snapshot rather than polling. The collector already knows when a snapshot lands;
the API needs to notice without coupling to the collector process. Simplest honest mechanism:
the API watches `max(snapshot_id)` on a short interval and pushes on change — that is still
polling, but **one poller instead of one per client**, and it is the change ENH-002 is actually
asking for.

### 4.5 — Auth and exposure (L)

**OPS-006 is a live finding and belongs here.** It was confirmed on 2026-07-26 that Streamlit
**does not** bind localhost only — `streamlit run app.py` advertises a Network URL and an
External URL, so it is reachable from the LAN today. ADR-012 makes localhost binding a hard
requirement before any further exposure. 4.5 fixes that for Streamlit *and* sets the same rule
for the API from the start, then documents the Tailscale path in `OPERATIONS.md`.

Auth is a single shared token, per ADR-012's single-user model. Not user accounts.

---

## Exit criteria

1. The Streamlit dashboard runs unchanged, and all six tabs render — verified on the real
   system after deploying, not just in tests.
2. `api/` imports no streamlit, asserted by `tests/test_layering.py`.
3. Every endpoint documented, with the contract in `docs/`.
4. Both servers bind localhost only.
5. The full check suite passes.

## Risks

- **Scope creep into M5.** Every endpoint invites "and then the UI could…". The gate exists
  because M5 is a decision point, not a foregone conclusion — the Streamlit ceiling is only
  worth resolving *if it still hurts*.
- **Two readers, one SQLite file.** The collector writes while the API reads. The dashboard
  already reads concurrently and this is a solved shape here, but it is worth proving under
  the API's access pattern rather than assuming it carries over.
- **4.3(b) would make M4 a writing milestone.** Flagged above; decide before starting.
