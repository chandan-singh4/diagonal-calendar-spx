"""
briefing.py — read the Gamma Exposure tab out loud, at chosen times of day.

STAGE ONE OF THE AI PANEL, AND DELIBERATELY NOT A PANEL. This is a command
you run and read in a terminal. If the output is vague or wrong we have spent
an evening rather than a week, and we will have learned it from real numbers
instead of from anybody's optimism about what a model can do.

    python -m scripts.briefing --times 09:45,15:00,15:30,15:45 \\
        --provider gemini --model <the model id on your key>

    python -m scripts.briefing --times 09:45 --dry-run   # prompt only, no call

**IT REPLAYS FROM THE RECORD.** `--times` are market-clock times and each one
is answered with the snapshot taken at or just before it, so a finished
session can be walked through after the close exactly as it would have been
read live. Nothing here polls, and nothing here writes.

THE CHAIN OF BRIEFINGS CARRIES FIGURES, NOT PROSE. Each call after the first
is shown the previous one's TIME, its one-line CALL and its headline numbers —
never its paragraphs. Handed its own paragraph a model acquires a position to
defend, and by the afternoon you are reading a view formed at ten o'clock and
defended ever since. Handed the previous numbers and told in as many words
that it is not required to agree, it compares instead, which is the whole
reason for running these in sequence.

WHAT THE MODEL IS NOT ALLOWED TO DO is stated in the system prompt below and
enforced by what it is given: every figure arrives finished from
`core/briefing.py`, including the pin candidate and the range. It has no raw
chain, so it cannot compute; it has no price history, so it cannot chart; and
the one judgement that looks like a forecast — is anything pinning — was made
in Python before the prompt was built.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sqlite3
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import db
from api import computed
from core import briefing as core_briefing
from dataaccess import queries
from integrations import llm, telegram

_MARKET_TZ = ZoneInfo(config.DISPLAY_TIMEZONE)

# The two moments the picture changes fastest, offered as the default so the
# common case needs no argument: 09:45 is the first reading where overnight
# positioning has met real volume, and the 15:30 run is where 0DTE charm goes
# vertical. Everything between them is the slow part of the day.
DEFAULT_TIMES = "09:45,15:00,15:30,15:45"

SYSTEM_PROMPT = """\
You explain an options dashboard to one trader. You are not a forecaster and \
you are not an adviser.

WHAT YOU ARE GIVEN
You receive a set of figures that have already been calculated from a live \
SPX option chain, arranged as a ladder in the order a trader reads them: \
volume, then delta, then gamma, then vanna, then charm. Each rung carries a \
"basis" sentence saying exactly what that measure is and what it cannot show. \
Read the basis before you use the figure.

THE RULES, AND THEY ARE ABSOLUTE
1. Use ONLY the numbers you were given. Never calculate a new one. Never \
estimate, round differently, or infer a level that is not in front of you.
2. If something is shown as an em dash, it could not be measured. Say it is \
not available. Do not treat it as zero and do not guess what it would be.
3. The pin candidate, the support and resistance levels, and the judgement of \
whether a pin is possible at all were ALL calculated for you. Never propose a \
different level. If you are told no pin is possible, the correct answer is \
that nothing is holding price today - not a weaker pin somewhere else.
4. Volume records how many contracts traded. It does NOT record who was \
buying or selling. Never describe volume as buying pressure or selling \
pressure.
5. No trade recommendations anywhere. No "you should", no strategy, no \
entry or exit. You explain what dealer hedging behaviour the figures imply; \
the trader decides what to do about it. The FORECAST block at the end is the \
ONLY place a price of your own may appear - keep it out of the explanation, \
which must describe the figures rather than argue for a target.
6. Every claim must be traceable to a figure you were given. If you cannot point at a number behind a sentence, do not write the sentence.

NUMBERS: LEVELS YES, SIZES NO. This is the single most important instruction about how it reads.
- A LEVEL is a price or a strike - spot, the pin candidate, the walls, the flip strike, a peak strike. These are the numbers the reader can act on. Keep them, written plainly: 7,700.
- A SIZE is a quantity - contract counts, dollar exposures in billions or trillions, ratios, percentages, totals. The reader has no yardstick for whether forty billion of gamma is a lot. NEVER print one. Say what it means instead: heavy, thin, one-sided, evenly matched, deeper than this morning, easing off.
- The ONE exception is a size that changed enough to matter, and then you say the DIRECTION, not the figure: "gamma got more negative through the afternoon", never "gamma moved from -35.7B to -42.4B".
- Comparisons between rungs are still allowed and still wanted. You compare the sizes silently and report the verdict.

SAY WHAT THE DEALERS ARE DOING. That is the whole point of the page. Every briefing must make these plain in ordinary words:
- Are dealers long gamma or short gamma, and therefore do they FADE moves (pushing price back, calming the day) or FOLLOW them (chasing price, making the day bigger)?
- Which way is the pressure leaning - up, down, or nowhere?
Write it the way one trader tells another across a desk. "Dealers are short gamma, so they sell into weakness - any drop feeds itself" is right. "Negative net GEX implies pro-cyclical hedging flows" is wrong.

HOW TO WRITE IT
Short sentences. No jargon unexplained. No hedging language. The reader knows markets but not mathematics and is reading on a phone with a minute to spare. The whole briefing must be UNDER 250 WORDS. If you are running long, cut detail, never cut the meaning.

Use exactly this structure:

WHAT CHANGED - two or three sentences. If you were given a previous briefing, say whether its call still holds and what turned. You are NOT required to agree with your earlier reading; if the picture changed, say so plainly. If there is no previous briefing, say what the day looks like from the start.

THE LADDER - ONE OR TWO SENTENCES PER RUNG. Not a paragraph. In order: volume, delta, gamma, vanna, charm. Skip any rung whose figures are all unavailable. Start each with the plain-word verdict, then the reason.
    Volume - how busy, and where the crowd is sitting.
    Delta - which way the book leans, up or down.
    Gamma - long or short, fade or follow. The most important rung.
    Vanna - what a change in nervousness would do to that.
    Charm - what the clock alone does into the close.
Where two rungs disagree, say so in one sentence. That disagreement is the most useful thing on the page.

WHAT THIS IMPLIES - what dealers do from here and between which levels, using only the pin figures you were handed. If no pin is possible, say moves are likely to extend rather than fade, and say what that feels like.

WHAT WOULD CHANGE IT - one or two things to watch, named as levels.

CALL: <one line, under fifteen words, the single sentence summary>

FORECAST - three lines, exactly these keys, numbers only, no words:
CLOSE: <your single best guess for today's SPX closing price>
RANGE: <low>-<high>  (where you expect the close to land)
CONFIDENCE: <low|medium|high>

THE FORECAST IS SCORED. It is written down and compared against the actual close, and a month of them is graded together. So it is a real prediction and not a hedge: give one number for CLOSE, not a range restated. A wide RANGE with low CONFIDENCE is the honest answer on a day you cannot read - say that rather than narrowing it to look decisive.

Write the forecast LAST, after the explanation is finished. Do not go back and adjust the explanation to agree with it. If your reasoning says the day is unreadable, the forecast should say so too.

The CALL line comes first of the four, then CLOSE, RANGE and CONFIDENCE.
"""


def _snapshot_at(db_path: str, session_date: str,
                 clock: str) -> tuple[int, str] | None:
    """The snapshot taken at or just before `clock` on `session_date`.

    AT OR BEFORE, never after. A briefing labelled 09:45 that was built from
    the 09:46 chain is a briefing that saw a minute the reader had not, which
    is exactly the mistake that makes a replay flatter itself.

    The stored stamps are naive UTC, so the comparison is done on the market
    clock and the returned label is market time — the whole script speaks in
    the times printed on the dashboard's own axis.
    """
    wanted = _dt.datetime.combine(
        _dt.date.fromisoformat(session_date),
        _dt.time.fromisoformat(clock), tzinfo=_MARKET_TZ,
    ).astimezone(_dt.UTC).replace(tzinfo=None)

    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT snapshot_id, snapshot_timestamp FROM snapshots "
            "WHERE status = 'COMPLETE' AND snapshot_timestamp <= ? "
            "ORDER BY snapshot_timestamp DESC LIMIT 1",
            (wanted.strftime("%Y-%m-%d %H:%M:%S"),),
        ).fetchone()

    if row is None:
        return None
    taken = (_dt.datetime.strptime(row["snapshot_timestamp"],
                                   "%Y-%m-%d %H:%M:%S")
             .replace(tzinfo=_dt.UTC).astimezone(_MARKET_TZ))
    # A snapshot from a previous session is not an answer to "what did 09:45
    # look like today" — it is silence, and it has to read as silence.
    if taken.date().isoformat() != session_date:
        return None
    return row["snapshot_id"], taken.strftime("%H:%M %Z")


def _figures(db_path: str, snapshot_id: int, clock: str) -> dict:
    """Build the ladder for one snapshot, through the tab's own functions.

    EVERY MEASURE COMES FROM `api.computed`, the same code the endpoints call.
    Recomputing any of it here would give the briefing a private opinion about
    what the dashboard shows, and the entire premise of this feature is that
    it cannot have one.
    """
    chain = queries.load_chain_df(db_path, snapshot_id)
    if chain.empty:
        raise SystemExit(f"snapshot {snapshot_id} holds no option rows")
    row = db.get_snapshot_by_id(db_path, snapshot_id)
    if row is None or row["underlying_price"] is None:
        raise SystemExit(f"snapshot {snapshot_id} records no underlying price")
    spot = float(row["underlying_price"])
    stamp = row["snapshot_timestamp"]

    shared = dict(r=config.RISK_FREE_RATE, q=config.DIVIDEND_YIELD,
                  snapshot_ts=stamp, display_tz=config.DISPLAY_TIMEZONE)

    return core_briefing.assemble(
        spot=spot,
        session_time=clock,
        gamma=computed.gamma_exposure(chain, spot, None, **shared),
        vgex=computed.volume_gamma_exposure(chain, spot, None, **shared),
        delta=computed.delta_exposure(chain, spot, None),
        vanna=computed.second_order_exposure(
            chain, spot, "vanna", stamp, r=config.RISK_FREE_RATE,
            q=config.DIVIDEND_YIELD, display_tz=config.DISPLAY_TIMEZONE),
        charm=computed.second_order_exposure(
            chain, spot, "charm", stamp, r=config.RISK_FREE_RATE,
            q=config.DIVIDEND_YIELD, display_tz=config.DISPLAY_TIMEZONE),
    )


def _carry(previous: dict | None) -> str:
    """The previous briefing, reduced to what may safely be carried forward.

    THE PROSE IS DROPPED ON PURPOSE — see this file's header. What survives is
    the clock, the one-line call, and the handful of figures a comparison
    actually needs, plus an explicit permission to disagree. That last line is
    not politeness: without it a model treats its own earlier call as a
    commitment and reads new data through it.
    """
    if previous is None:
        return "PREVIOUS BRIEFING: none. This is the first reading of the day."
    return (
        "PREVIOUS BRIEFING\n"
        f"  time: {previous['as_of']}\n"
        f"  call: {previous['call']}\n"
        f"  figures then: {json.dumps(previous["figures"], indent=2, ensure_ascii=False)}\n"
        "You are not required to agree with this. State plainly whether it "
        "still holds, and what changed."
    )


def _headline(figures: dict) -> dict:
    """The few numbers worth carrying between briefings.

    Deliberately small. Carrying the whole ladder forward would make each
    prompt a transcript of the day and give the model five chances to
    reconcile figures it was not asked to compare.
    """
    gamma = next(r for r in figures["ladder"] if r["name"] == "gamma")
    return {
        "spot": figures["spot"],
        "net_gex": gamma["figures"].get("net_gex", "—"),
        "flip_strike": gamma["figures"].get("flip_strike", "—"),
        "pin_candidate": figures["pin"].get("candidate"),
        "pin_possible": figures["pin"].get("pin_possible"),
    }


# WHERE THE FORECASTS GO, AND WHY IT IS NOT THE DATABASE. `data/dashboard.db`
# holds what the broker sent. A machine's guess about the close is a different
# kind of thing, and once the two share a table nothing downstream can tell
# them apart — a query that accidentally treats a prediction as an observation
# is a bug with no symptom. A separate append-only file also means the whole
# experiment can be deleted by deleting one file.
FORECAST_LOG = Path(config.PROJECT_ROOT) / "data" / "briefing_forecasts.jsonl"


def _extract_forecast(text: str) -> dict[str, object]:
    """The CLOSE, RANGE and CONFIDENCE lines, or blanks where they are absent.

    PARSED LENIENTLY, RECORDED STRICTLY. A model that writes "CLOSE: 7,690"
    or "CLOSE: ~7690" has still made a prediction and it should be scored;
    one that writes a paragraph where a number belongs has not, and that
    stores as None rather than as a number scraped out of prose. A forecast
    log that quietly invents predictions the model never made would grade
    something other than the model.
    """
    out: dict[str, object] = {"close": None, "low": None, "high": None,
                              "confidence": None}

    def number(raw: str) -> float | None:
        cleaned = re.sub(r"[^0-9.]", "", raw.replace(",", ""))
        try:
            return float(cleaned)
        except ValueError:
            return None

    for line in text.splitlines():
        stripped = line.strip().lstrip("*# ").strip()
        upper = stripped.upper()
        if upper.startswith("CLOSE:"):
            out["close"] = number(stripped.split(":", 1)[1])
        elif upper.startswith("RANGE:"):
            body = stripped.split(":", 1)[1]
            # An en dash and a hyphen both appear in practice, and "7690 to
            # 7710" appears too. Split on any of them rather than requiring
            # one, then take the first two numbers found.
            parts = [number(p) for p in re.split(r"[-–—]| to ", body)]
            found = [v for v in parts if v is not None]
            if len(found) >= 2:
                out["low"], out["high"] = min(found[:2]), max(found[:2])
        elif upper.startswith("CONFIDENCE:"):
            word = stripped.split(":", 1)[1].strip().lower()
            out["confidence"] = word if word in {"low", "medium", "high"} else None
    return out


def _record(entry: dict) -> None:
    """Append one briefing's prediction to the log. Never rewrites, never
    reads back — grading is a separate command over the finished file."""
    FORECAST_LOG.parent.mkdir(parents=True, exist_ok=True)
    with FORECAST_LOG.open("a", encoding="utf-8") as handle:
        print(json.dumps(entry, ensure_ascii=False), file=handle)


def for_telegram(clock: str, taken: str, figures: dict, forecast: dict,
                  answer) -> str:
    """The briefing as one phone message.

    LIVES HERE RATHER THAN IN THE DAEMON because both callers need it: the
    daemon posts through the session, and this script's --telegram replays a
    finished day. Two copies would drift, and the drift would be invisible —
    a replayed briefing and a live one would simply look slightly different
    on the phone with nothing to say which was right.

    A HEADER THE MODEL DID NOT WRITE. Spot, the time and the model's name are
    facts this process knows and the model only repeats, so they are stated
    here rather than trusted to the prose — and the model name matters on a
    phone in a way it does not in a terminal: an answer from the fourth rung
    of the chain reads exactly like one from the first.

    THE FORECAST IS RESTATED AT THE FOOT even though it is already in the
    text. On a phone the text is scrolled and the forecast is the line most
    often wanted at a glance, and repeating three numbers is cheaper than
    hunting for them.
    """
    pin = figures["pin"]
    verdict = ("no pin — moves extend" if not pin["pin_possible"]
               else f"pin candidate {pin['candidate']}, "
                    f"{pin['support']}–{pin['resistance']}")
    head = (f"SPX BRIEFING · {clock}\n"
            f"spot {figures['spot']}  ·  snapshot taken {taken}\n"
            f"{verdict}\n"
            f"{'─' * 32}\n")
    # Blank rather than a number when the model declined to forecast: an
    # absent prediction and a prediction of nothing are different, and the
    # scoring log already refuses to grade the first as though it were real.
    tail = ("\n" + "─" * 32 + "\n"
            f"close {forecast['close'] or '—'}  ·  "
            f"range {forecast['low'] or '—'}–{forecast['high'] or '—'}  ·  "
            f"{forecast['confidence'] or '—'} confidence\n"
            f"[{answer.provider} / {answer.model}]")
    return head + answer.text + tail


def _extract_call(text: str) -> str:
    """The last CALL: line, or an honest note that the model did not write one.

    Searched from the END because a model that mentions the format while
    explaining itself will produce the word twice, and the one that matters is
    the one it finished on.
    """
    for line in reversed(text.splitlines()):
        if line.strip().upper().startswith("CALL:"):
            return line.strip()[5:].strip()
    return "(the model did not produce a CALL line)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None, help=
                        "session to read, YYYY-MM-DD. Defaults to the session "
                        "of the newest snapshot in the record.")
    parser.add_argument("--times", default=DEFAULT_TIMES, help=
                        "comma-separated market-clock times, e.g. "
                        f"'{DEFAULT_TIMES}'")
    parser.add_argument("--model", default=None, help=
                        "pin the head of the fallback chain to this exact "
                        "model id. Omitted, the chain starts at the newest "
                        "Gemini Flash on the live roster and falls through "
                        "OpenRouter's free models and Groq's from there. "
                        "Nothing is hardcoded — the free roster churns "
                        "weekly. See services/llm.py.")
    parser.add_argument("--telegram", action="store_true", help=
                        "post each briefing to the Telegram report channel as "
                        "well as printing it. A replay posts the same message "
                        "the daemon would have sent live, which is the point: "
                        "the phone format is judged on a real day before the "
                        "daemon is trusted to send it unattended.")
    parser.add_argument("--dry-run", action="store_true", help=
                        "print the assembled figures and the prompt, and "
                        "call no provider.")
    args = parser.parse_args(argv)

    # THE CONSOLE HAS TO BE UTF-8 BEFORE ANYTHING PRINTS. Windows hands Python
    # a cp1252 stdout, and the em dash is not decoration here — it is the
    # project's blank-not-zero marker, so every unmeasurable figure in the
    # ladder carries one. Left alone this dies on the first blank with a
    # UnicodeEncodeError, which reads like a data fault and is a terminal one.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    db_path = config.DB_PATH
    session_date = args.date
    if session_date is None:
        latest = db.get_latest_complete_snapshot(db_path)
        if latest is None:
            print("The record holds no completed snapshot.", file=sys.stderr)
            return 1
        session_date = (
            _dt.datetime.strptime(latest["snapshot_timestamp"],
                                  "%Y-%m-%d %H:%M:%S")
            .replace(tzinfo=_dt.UTC).astimezone(_MARKET_TZ).date().isoformat())

    if args.telegram and not telegram.configured():
        # REFUSED UP FRONT, NOT PER-SLOT. Asked for Telegram and given none,
        # the run would otherwise call six models, print six briefings, and
        # deliver nothing to the phone the request was about — with the
        # failure visible only as an absence.
        print("--telegram needs TELEGRAM_BOT_TOKEN and "
              "TELEGRAM_REPORT_CHAT_ID in .env. See telegram_setup.bat.",
              file=sys.stderr)
        return 3

    print(f"Session {session_date}")
    if not args.dry_run:
        # THE CHAIN IS PRINTED BEFORE ANYTHING IS ASKED. It is built from three
        # live rosters, so it is different today from yesterday — and when a
        # briefing comes back thin, the first question is which model wrote it.
        # Answering that after the fact means fetching the rosters again, by
        # which time they may have changed.
        rungs = llm.chain(args.model)
        if not rungs:
            print("No usable model — see the key names in services/llm.py.",
                  file=sys.stderr)
            return 2
        print("Chain: " + " → ".join(f"{m.provider}/{m.id}" for m in rungs[:4])
              + (f"  (+{len(rungs) - 4} more)" if len(rungs) > 4 else ""))
    print()

    previous: dict | None = None
    for clock in [t.strip() for t in args.times.split(",") if t.strip()]:
        found = _snapshot_at(db_path, session_date, clock)
        if found is None:
            print(f"── {clock} — no snapshot at or before this time\n")
            continue
        snapshot_id, taken = found
        figures = _figures(db_path, snapshot_id, clock)

        prompt = (f"{_carry(previous)}\n\n"
                  f"CURRENT FIGURES (snapshot {snapshot_id}, taken {taken})\n"
                  f"{json.dumps(figures, indent=2, ensure_ascii=False)}")

        print("═" * 72)
        print(f"  {clock}  ·  snapshot {snapshot_id}  ·  taken {taken}")
        print("═" * 72)

        if args.dry_run:
            print(prompt)
            print()
            # The carried call is faked in a dry run so the chaining itself is
            # exercised: the second prompt has to be shown to CONTAIN a
            # previous block, or the dry run has not checked the thing most
            # likely to be wrong.
            previous = {"as_of": clock, "call": "(dry run — no model called)",
                        "figures": _headline(figures)}
            continue

        try:
            answer = llm.complete(SYSTEM_PROMPT, prompt,
                                  preferred=args.model)
        except llm.LLMError as exc:
            # ONE FAILED BRIEFING DOES NOT END THE RUN. The next time slot is
            # a fresh chain against rosters that may have recovered, and a gap
            # in a replay is more useful than an aborted one.
            print(f"  no model answered: {exc}\n")
            continue

        for declined in answer.attempts:
            print(f"  (skipped {declined})")

        print(answer.text)
        forecast = _extract_forecast(answer.text)
        call = _extract_call(answer.text)
        _record({
            "session_date": session_date, "as_of": clock,
            "snapshot_id": snapshot_id, "taken": taken,
            "spot_at_briefing": figures["spot"],
            "provider": answer.provider, "model": answer.model,
            "call": call, **forecast,
            # The whole text is kept so a bad forecast can be read back and
            # understood rather than only counted. It is evidence about the
            # model, not data about the market.
            "text": answer.text,
        })
        print()
        print(f"  [{answer.provider} / {answer.model}] forecast logged: "
              f"close={forecast['close']} "
              f"range={forecast['low']}-{forecast['high']} "
              f"confidence={forecast['confidence']}")
        print()
        # AFTER THE LOG, EXACTLY AS IN THE DAEMON. The forecast is already on
        # disk by the time the phone is involved, so a Telegram failure during
        # a replay costs a notification and never a scored briefing.
        if args.telegram and not telegram.send(
                for_telegram(clock, taken, figures, forecast, answer)):
            print("  (telegram post failed — the briefing is still logged)")

        previous = {"as_of": clock, "call": call,
                    "figures": _headline(figures)}

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
