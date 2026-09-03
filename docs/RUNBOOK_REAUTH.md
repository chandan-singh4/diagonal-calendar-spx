# Runbook — renewing the Schwab permission (weekly)

**Task M3.8.** Written 2026-09-03. Read this one file; it is self-contained.
**Time needed: about two minutes.** No database is touched and the collector is never stopped.

---

## What this is, in plain terms

The broker will not let a program read prices forever on one approval. When you log in through
the browser, Schwab issues a **permission slip** that the collector carries on every request.
**That slip lasts exactly 7 days** and then stops working. Renewing it means logging in through
the browser again, by hand.

**Schwab sets the 7 days. Nothing in this project can extend it, and no script can renew it
without you** — the login needs a real browser and a real person. That is a deliberate security
boundary on their side, not a gap in this code.

**If it lapses, the collector goes blind.** It keeps running and keeps trying, but no prices are
recorded until the slip is renewed. Prices missed during a lapse are **gone for good** — the
broker does not sell you last Tuesday's quotes. This is the single most damaging thing that can
happen to the project through neglect, and it happened once already: 2026-08-09.

---

## How you find out it is due

Three things tell you, and they are deliberately not the same thing:

| Where | When | What it says |
|---|---|---|
| **Dashboard banner** (top of the page) | from **day 6** | Yellow warning with days left. Turns into a red "TOKEN EXPIRED" bar past day 7. |
| **Watchdog** — desktop pop-up **and** email | any time, checks every 10 minutes | Names the command to run. Works whether or not the dashboard is open. |
| **You ask it yourself** | any time | `python scripts/reauth.py --check` — see below. |

The banner alone is not enough and never was: **an alarm you only see by opening a page is not an
alarm.** That is why the watchdog exists (ADR-045).

### Asking it yourself

From the project folder:

```
C:\Users\chand\Python\.venv\Scripts\python.exe scripts\reauth.py --check
```

It prints the age, the days remaining, and the exact date and time it runs out. **It changes
nothing** — it is safe to run at any moment, including mid-session with the collector running.

---

## The renewal — step by step

**Best time to do it: outside market hours,** or at least not in the first or last half hour of
a session. Nothing breaks if you do it mid-session, but there is no reason to be renewing a
permission while prices you care about are moving.

1. **Open a terminal in the project folder**
   `C:\Users\chand\Python\spx-diagonal-dashboard`

2. **Run:**
   ```
   C:\Users\chand\Python\.venv\Scripts\python.exe scripts\reauth.py
   ```

3. **The script sets your current slip aside** (it copies it to a `.bak-` file first — it does
   not throw it away) and opens your browser at the Schwab login.

4. **Log in and click Allow.**

5. **The page will then look like an error page. That is expected and correct** — it is your own
   machine at `https://127.0.0.1:8182`, and there is nothing there to serve a page. **What
   matters is the address bar.**

6. **Copy the FULL address from the address bar** — the whole thing, including everything after
   the `?` — and paste it back into the terminal, then press Enter.

7. **Done.** The script confirms success and prints the date the new slip expires, plus a
   suggested reminder date one day before.

**The collector picks the new slip up on its next cycle by itself. Do not restart it.**

---

## If something goes wrong

**You can abort at any point.** Close the browser, or press Ctrl+C in the terminal. **The script
puts your old slip back**, and if it still had time left the collector carries on uninterrupted.
That safety net is the main reason this script exists rather than a list of manual steps —
aborting halfway used to leave you with *no* permission at all, which is strictly worse than the
nearly-expired one you started with.

**"The login reported success but no token file was written"** — rare; the script restores the
old slip and exits. Simply run it again.

**The browser never opens, or Schwab rejects the redirect.** Check `.env`:
`SCHWAB_CALLBACK_URL` must match what is registered on the Schwab developer portal **character
for character** (default `https://127.0.0.1:8182`). This only breaks if something changed on the
portal — it does not drift on its own.

**Nothing works and the login is refused outright.** Check `SCHWAB_APP_KEY` and
`SCHWAB_APP_SECRET` in `.env`. These are the *app registration*, a different thing from the
weekly slip, and they do **not** run out on the 7-day clock. If they are the problem, something
changed at <https://developer.schwab.com>.

---

## One thing never to do

**Do not run this:**

```
python -c "import schwab_client; schwab_client.get_client()"
```

It looks like it should re-authenticate. **It does not.** `get_client()` only starts a login when
the token file is *missing* — and it is still there, stale, right up until it expires. So the
command loads the dead slip, prints nothing, exits cleanly, and you believe you are renewed.
You find out on Monday morning when the collector has recorded nothing.

The dashboard's own banner recommended exactly that command until 2026-07-26, so this trap was
actively being taught. `scripts/reauth.py` exists to make the wrong way hard to reach.

---

## Related

`scripts/reauth.py` (the script, with its reasoning in the docstring) ·
`scripts/watchdog.py` (the alarm, ADR-045) · `ui/header.py` `render_token_banner` (the banner) ·
`schwab_client.get_token_age_days` (where the 7-day clock is read from — the token's
`creation_timestamp`, which routine refreshes do **not** reset, so it tracks the real deadline).
