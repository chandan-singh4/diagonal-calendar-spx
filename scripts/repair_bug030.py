"""BUG-030 row repair: the broker's no-value marker -> NULL. RUN ONCE, 2026-09-03.

Kept rather than deleted because it is the record of HOW the one irreplaceable
file was edited, and because it refuses to run a second time -- the id counts
are asserted, so a repeat finds 0/0 and exits rather than touching anything.

Two phases, deliberately. The 18.7M-row SCAN is done first on a READ-ONLY
connection, which under WAL takes no write lock at all (~46s). Only the ids
come back, and the write is then `where id in (...)` against the primary key --
milliseconds, not a minute. Doing it as one big UPDATE would hold the write
lock for the whole scan, and the collector gives up after 15 seconds
(db.py:227), so an ordinary poll would have failed and left a gap row.

Exact equality throughout. theta = -9.99 is real data (38 rows) and is not
touched; only theta = -999.0 is the marker.
"""
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# This import must follow the sys.path shim above.
import config

IV_MARK = -9.99          # the marker AFTER the collector's /100
GK_MARK = -999.0         # greeks are not divided, so they keep its raw shape
RO = f"file:{config.DB_PATH}?mode=ro"

def phase(msg): print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)

# ── 1. Find them, holding no lock ────────────────────────────────────────────
phase("scanning option_rows read-only ...")
t = time.time()
ro = sqlite3.connect(RO, uri=True)
opt_ids = [r[0] for r in ro.execute(
    """select id from option_rows
       where iv = ? or delta = ? or gamma = ? or theta = ? or vega = ?""",
    (IV_MARK, GK_MARK, GK_MARK, GK_MARK, GK_MARK))]
atm_ids = [r[0] for r in ro.execute(
    """select id from atm_iv_by_expiry
       where atm_call_iv = ? or atm_put_iv = ? or atm_avg_iv = ?""",
    (IV_MARK, IV_MARK, IV_MARK))]
safe_theta = ro.execute("select count(*) from option_rows where theta = ?",
                        (IV_MARK,)).fetchone()[0]
ro.close()
phase(f"option_rows: {len(opt_ids):,}   atm_iv_by_expiry: {len(atm_ids):,}   "
      f"({time.time()-t:.0f}s)")
phase(f"legitimate theta = -9.99 rows that must survive: {safe_theta}")

if (len(opt_ids), len(atm_ids)) != (5127, 14):
    sys.exit(f"REFUSING: expected 5127/14, found {len(opt_ids)}/{len(atm_ids)}")

# ── 2. Wait for room between polls ───────────────────────────────────────────
# The collector polls at :HH:M2:18 on the 5-minute MIDDAY cadence. The write
# below is indexed and takes well under a second, but there is no reason to
# start it in the same breath as a poll.
now = datetime.now()
into = (now.minute % 5) * 60 + now.second
gap = 300 - into
phase(f"{gap}s until the next poll")
if gap < 60:
    sys.exit("REFUSING: too close to a poll; run again in a minute")

# ── 3. Write ─────────────────────────────────────────────────────────────────
def chunks(xs, n=500):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]

conn = sqlite3.connect(config.DB_PATH, timeout=30)
conn.execute("pragma busy_timeout = 30000")
t = time.time()
try:
    with conn:
        for batch in chunks(opt_ids):
            q = ",".join("?" * len(batch))
            conn.execute(f"""
                update option_rows set
                    iv    = case when iv    = ? then null else iv    end,
                    delta = case when delta = ? then null else delta end,
                    gamma = case when gamma = ? then null else gamma end,
                    theta = case when theta = ? then null else theta end,
                    vega  = case when vega  = ? then null else vega  end
                where id in ({q})""",
                (IV_MARK, GK_MARK, GK_MARK, GK_MARK, GK_MARK, *batch))
        for batch in chunks(atm_ids):
            q = ",".join("?" * len(batch))
            # The two derived columns are computed FROM the marker, so they are
            # nonsense wherever it appears (-10.18, a ratio of -52) and go with it.
            conn.execute(f"""
                update atm_iv_by_expiry set
                    atm_call_iv        = case when atm_call_iv = ? then null else atm_call_iv end,
                    atm_put_iv         = case when atm_put_iv  = ? then null else atm_put_iv  end,
                    atm_avg_iv         = case when atm_avg_iv  = ? then null else atm_avg_iv  end,
                    iv_spread_to_front = null,
                    iv_ratio_to_front  = null
                where id in ({q})""",
                (IV_MARK, IV_MARK, IV_MARK, *batch))
    phase(f"committed in {time.time()-t:.1f}s")
finally:
    conn.close()
