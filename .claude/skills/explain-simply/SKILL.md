---
name: explain-simply
description: >-
  Explain any topic in plain, everyday language that a complete newcomer can
  follow, using analogies and concrete examples so the idea actually clicks.
  Use this whenever the user asks you to "explain," "break down," "help me
  understand," "ELI5," "what is / how does X work," "make this make sense," or
  otherwise signals they want to *understand* something rather than get a
  terse fact — including dense technical, legal, financial, scientific, or
  academic material, error messages, code, papers, or jargon-heavy text they
  paste in. Reach for it even when the user doesn't say the word "simple":
  if the goal is comprehension, this is the skill. Do not use it for tasks
  that aren't about understanding (writing code to spec, formatting data,
  pure retrieval of a single fact).
---

# Explain Simply

Your job here is to make an idea *land* for someone who starts out knowing
nothing about it. Success isn't "technically complete" — it's "the reader now
gets it and could re-explain the gist to a friend." Optimize for that feeling
of the penny dropping.

## The core move: translate, don't dumb down

Simple is not the same as shallow. You are not removing the real idea — you're
carrying it across a bridge into words and pictures the reader already owns.
The content stays honest; only the packaging changes. If a simplification would
make the reader believe something false, it's gone too far. Aim for "true, just
told in plain clothes."

Two failure modes sit on either side, and you're steering between them:
- **Too dense:** jargon, assumed background, abstraction with nothing to hold
  onto. The reader bounces off.
- **Too dumbed-down:** so vague or cutesy it's condescending, or so lossy it's
  now wrong. The reader feels talked down to, or walks away misinformed.

## The method

Work through these as needed — not every explanation needs all of them, but
this is the reliable order when something is genuinely hard.

1. **Lead with the point and why it matters.** Before any detail, say what the
   thing *is for* or why anyone cares in one plain sentence. People hold onto
   detail far better once they know what it's building toward.
2. **Anchor to something they already know.** Find the everyday thing this
   idea resembles and start there. The reader's existing knowledge is the
   scaffolding; you're just hanging the new idea on it. (See the analogy
   toolkit below.)
3. **Show a concrete instance before the abstract rule.** A specific example
   first, the general principle second. Minds grab the particular and
   generalize from it far more easily than the reverse.
4. **Introduce one new idea at a time.** Build in small steps, each resting on
   the last. If a sentence needs two unfamiliar ideas to make sense, split it.
5. **Translate every unavoidable term on first use.** Don't dodge the real
   word if the reader will meet it again — name it, then immediately say what
   it means in plain language: "a *hash* — basically a fingerprint for a chunk
   of data." Now they own the term instead of fearing it.
6. **Point out where the analogy breaks.** Every analogy leaks. Briefly flag
   the seam ("this is where the comparison stops holding") so no one over-
   trusts it and gets confused later.
7. **Close with a one-line recap.** A single "so, in short…" sentence that the
   reader could repeat back. This is the thing they'll actually keep.

## The toolkit

Named techniques to pull from. Each is a tool, not a rule — grab the ones that
fit.

**Anchor to the familiar (analogy / metaphor).** Map the unknown onto the
known. Choose an anchor from ordinary life — kitchens, mail, traffic, water,
libraries, money — that shares the *shape* of the idea, not just a surface
vibe.
> *Encryption is like a locked mailbox: anyone can drop a letter in through the
> slot (encrypt), but only the person with the key can open it and read what's
> inside (decrypt).*

**Concrete-first.** Open with one vivid, specific case, then zoom out to the
general rule. "Say you deposit $100 at 5% interest…" beats "Compound interest
is the accrual of interest on principal and prior interest."

**The Feynman check.** After drafting, reread as if you're a curious
twelve-year-old. Any spot where you'd go "wait, what does that word mean?" is a
gap — patch it with plainer words or an example. If *you* can only explain a
piece by parroting the textbook phrasing, you haven't understood it plainly
enough yet; dig until you can.

**Name-and-translate jargon.** Don't pretend technical terms don't exist —
readers hit them in the wild. Introduce the term, then instantly gloss it in
everyday words so it stops being a wall.

**Contrast pairs.** Define by what something *is* versus what it's *often
confused with*. "Weather is what you get today; climate is what you expect over
years." Boundaries make a fuzzy idea sharp.

**Chunk and sequence.** Break a big thing into 3–5 labeled pieces and walk them
in order. A pipeline, a recipe, a "first this, then that" story — structure the
reader can hold in their head.

**Tell it as a small story.** Give the idea characters and motion where it
fits. "A request leaves your browser, knocks on the server's door, and waits
for an answer" carries more than a static definition.

## Tone

- **Neutral and friendly, like a knowledgeable friend** — warm, relaxed, on the
  reader's side. Never a lecture.
- **Never pedantic or showy.** The goal is their understanding, not a display
  of yours. Cut throat-clearing, hedging, and "well, actually."
- **No condescension.** Simple language, full respect. Don't announce that
  you're simplifying ("in layman's terms, for someone like you…") — just do it.
  Skip the baby talk; a smart adult who happens to be new to the topic is the
  reader.
- **Plain words over impressive ones.** "Use," not "utilize." Short sentences.
  If a fancy word earns its place, keep it — and translate it.
- **Match their level.** Read the cues. A beginner and an expert asking about
  their own field need different depth; adjust rather than defaulting to one
  register.

## Traps to avoid

- **Lossy-to-wrong.** Cutting nuance until the statement is actually false.
  Keep it simple *and* true; if you must drop something important, say you're
  simplifying and roughly which direction the truth lies.
- **Analogy that quietly misleads.** A comparison that fits at first but leads
  the reader to a wrong conclusion downstream. Pick a better anchor, or flag
  the seam.
- **Jargon smuggling.** Using a term you never defined because it feels basic
  *to you*. The curse of knowledge is the enemy — assume less shared context
  than feels natural.
- **The wall of text.** Even plain words drown in a dense block. Use short
  paragraphs, a list, or a step-by-step when the shape calls for it.
- **Explaining more than was asked.** Answer the actual question first. Offer to
  go deeper rather than front-loading every caveat.

## A quick before / after

**Topic:** What is an API?

**Before (dense):**
> An API is a defined interface exposing a set of endpoints that abstract
> underlying implementation, allowing programmatic interoperability between
> heterogeneous systems via structured request/response contracts.

**After (this skill):**
> An **API** is a way for two pieces of software to talk to each other without
> needing to know how the other one works inside.
>
> Think of a restaurant. You (one program) don't walk into the kitchen and cook
> — you hand the *waiter* your order, and the waiter brings back the dish. The
> API is the waiter: a middleman with a fixed menu of things you're allowed to
> ask for. You say "I'd like today's weather for Tampa," it goes off to the
> kitchen, and it hands you back the answer in a tidy format.
>
> The nice part: you never have to know how the kitchen cooks. As long as the
> menu (the API) stays the same, the kitchen can completely change how it works
> and your order still goes through. (Where the analogy stops: a real API can
> handle thousands of "orders" a second and never gets tired or takes a break.)
>
> So, in short: an API is a menu of requests one program offers to others, so
> they can get what they need without touching each other's inner workings.

Notice what the "after" does: leads with the point, anchors to a restaurant,
translates the term, uses a concrete order, flags where the analogy breaks, and
closes with a one-liner. That's the whole method in one small example.
