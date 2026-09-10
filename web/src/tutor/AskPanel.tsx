/**
 * The Ask panel — a tutor that can see this snapshot's figures and nothing else.
 *
 * A SLIDE-OVER ON THE RIGHT RATHER THAN A TAB. The question a reader wants to
 * ask is almost always about the chart they are looking at, and a tab would
 * take that chart off the screen to answer it. The panel overlays; the board
 * stays where it was.
 *
 * IT SENDS THE WHOLE VISIBLE CONVERSATION BACK EVERY TIME, and holds no state
 * on the server. `POST /mission/ask` is stateless on purpose: the figures move
 * underneath the chat every time the collector writes, so the server rebuilds
 * the ladder from the CURRENT snapshot on every turn and treats the history as
 * context rather than as the subject. An answer given at 10:02 and a follow-up
 * at 15:58 are read against different boards, which is correct, and is why
 * there is no session id anywhere in this file.
 *
 * A FAILED TURN IS NOT KEPT. When every free provider is busy the question is
 * put back in the box rather than left in the transcript looking answered —
 * otherwise the next turn's context contains a question with no reply, and the
 * model tries to explain the silence.
 *
 * THE MODEL AND EFFORT PICKS SURVIVE A RELOAD, in localStorage. They are
 * preferences about how you want to be answered, not part of a conversation,
 * and having to reset them every morning is how a control stops being used.
 */
import { useEffect, useRef, useState } from 'react'

import { ApiError, useAsk, useTutorModels } from '../api/client'
import type { Effort, TutorTurn } from '../api/types'
import { type Dictation, listen, supported as dictationSupported } from './Dictation'

/** Offered because the hard part of a blank chat box is knowing what to ask,
 *  and because each of these is a question the figures can genuinely answer.
 *  None asks for a trade — the server is required to refuse those, and a
 *  suggested question that gets refused would teach the reader on their very
 *  first click that the panel is broken. */
const STARTERS = [
  'What is the picture right now, in plain English?',
  'Are dealers long or short gamma, and what does that mean for me?',
  'What is charm, and what is it doing today?',
  'What is vanna, and what is it telling me today?',
  'Which two readings disagree with each other right now?',
]

/** WHAT EACH LEVEL ACTUALLY DOES, said on the control rather than left to be
 *  guessed. The level does two things at once: it travels to the provider as a
 *  real reasoning budget (`reasoning_effort`, or `reasoning: {effort}` on
 *  OpenRouter) which governs how long the model THINKS, and it shapes the
 *  written answer through the prompt. The hints below describe the second,
 *  because that is the part the reader sees. */
const EFFORTS: { id: Effort; label: string; hint: string }[] = [
  { id: 'low', label: 'Low', hint: 'Two or three sentences. Answer and stop.' },
  { id: 'medium', label: 'Medium', hint: 'A couple of short paragraphs. The default.' },
  { id: 'high', label: 'High', hint: 'Takes the rungs one at a time and names the disagreements.' },
  { id: 'max', label: 'Max', hint: 'Walks the whole ladder, defining each term. For studying.' },
]

const MODEL_KEY = 'spx.tutor.model'
const EFFORT_KEY = 'spx.tutor.effort'

function stored(key: string): string | null {
  try {
    return window.localStorage.getItem(key)
  } catch {
    // Private windows and blocked site data both throw here. A preference
    // that cannot be remembered is not a reason to fail to draw the panel.
    return null
  }
}

function remember(key: string, value: string | null) {
  try {
    if (value === null) window.localStorage.removeItem(key)
    else window.localStorage.setItem(key, value)
  } catch {
    // Same as above: the pick still applies to this session.
  }
}

/** What went wrong, in words that say what to do about it.
 *
 *  A 404 HERE MEANS ONE SPECIFIC THING and it is not "the page is broken":
 *  the API process is running code older than this panel, because uvicorn was
 *  started before /mission/ask existed and does not reload itself. The raw
 *  "Not Found" sent the reader looking at the chat for a fault that is in a
 *  terminal window (2026-09-08, twice). A 503 is the ordinary one — every free
 *  provider busy at the same moment — and is genuinely worth retrying. */
function explainFailure(error: unknown): { title: string; hint: string } {
  if (error instanceof ApiError && error.status === 404) {
    return {
      title: 'The server does not have this endpoint yet.',
      hint: 'The API process is running older code. Restart it: '
        + 'uvicorn api.app:app --host 127.0.0.1 --port 8899 --reload',
    }
  }
  if (error instanceof ApiError && error.status === 503) {
    return {
      title: error.message,
      hint: 'Every free model was busy at once. Your question is back in the box — send it again.',
    }
  }
  return {
    title: error instanceof Error ? error.message : 'The question could not be answered.',
    hint: 'Your question is back in the box — send it again.',
  }
}

function Bubble({ turn }: { turn: TutorTurn }) {
  const mine = turn.role === 'user'
  return (
    <div className="flex" style={{ justifyContent: mine ? 'flex-end' : 'flex-start' }}>
      <div
        className="max-w-[85%] whitespace-pre-wrap rounded-[10px] px-3 py-2 text-[13px] leading-[1.55]"
        style={{
          background: mine ? 'var(--blue)' : 'var(--bg-card)',
          color: mine ? '#fff' : 'var(--text)',
          border: mine ? 'none' : '1px solid var(--border)',
        }}
      >
        {turn.content}
      </div>
    </div>
  )
}

const CHIP: React.CSSProperties = {
  background: 'var(--bg-card)',
  border: '1px solid var(--border)',
  color: 'var(--text)',
  borderRadius: 6,
  padding: '4px 8px',
  fontSize: 11,
  cursor: 'pointer',
}

/** The model list, grouped by provider, in the order the chain walks them.
 *
 *  ORDER IS INFORMATION HERE and is deliberately not sorted alphabetically:
 *  the server returns the fallback chain, so the top of this list is what
 *  answers when nothing is picked, and the rest is what answers when it is
 *  busy. Sorting by name would throw that away. */
function ModelPopup({
  picked, onPick, onClose,
}: { picked: string | null; onPick: (id: string | null) => void; onClose: () => void }) {
  const { data, isPending, isError } = useTutorModels()

  return (
    <div
      className="absolute bottom-full left-0 z-10 mb-2 max-h-[320px] w-[320px] overflow-y-auto rounded-[8px] p-1"
      style={{ background: 'var(--bg)', border: '1px solid var(--border)' }}
      role="listbox"
      aria-label="Model"
    >
      <button
        type="button"
        onClick={() => { onPick(null); onClose() }}
        className="w-full rounded-[6px] px-2 py-2 text-left text-[12px]"
        style={{ background: picked === null ? 'var(--bg-card)' : 'transparent', color: 'var(--text)' }}
      >
        Auto
        <div className="text-[10px]" style={{ color: 'var(--text-3)' }}>
          {data?.default ? 'Currently ' + data.default : 'The head of the fallback chain'}
        </div>
      </button>

      {isPending && (
        <div className="px-2 py-2 text-[11px]" style={{ color: 'var(--text-3)' }}>
          Reading the free rosters&hellip;
        </div>
      )}
      {isError && (
        <div className="px-2 py-2 text-[11px]" style={{ color: 'var(--text-3)' }}>
          The roster could not be read. Auto still works.
        </div>
      )}

      {data?.models.map((model) => (
        <button
          key={model.provider + '/' + model.id}
          type="button"
          onClick={() => { onPick(model.id); onClose() }}
          className="w-full rounded-[6px] px-2 py-2 text-left text-[12px]"
          style={{
            background: picked === model.id ? 'var(--bg-card)' : 'transparent',
            color: 'var(--text)',
          }}
        >
          {model.id}
          <div className="text-[10px]" style={{ color: 'var(--text-3)' }}>
            {model.provider} · {Math.round(model.context / 1000)}k context
          </div>
        </button>
      ))}
    </div>
  )
}

function EffortPopup({
  picked, onPick, onClose,
}: { picked: Effort; onPick: (e: Effort) => void; onClose: () => void }) {
  return (
    <div
      className="absolute bottom-full left-0 z-10 mb-2 w-[300px] rounded-[8px] p-1"
      style={{ background: 'var(--bg)', border: '1px solid var(--border)' }}
      role="listbox"
      aria-label="Effort"
    >
      {EFFORTS.map((effort) => (
        <button
          key={effort.id}
          type="button"
          onClick={() => { onPick(effort.id); onClose() }}
          className="w-full rounded-[6px] px-2 py-2 text-left text-[12px]"
          style={{
            background: picked === effort.id ? 'var(--bg-card)' : 'transparent',
            color: 'var(--text)',
          }}
        >
          {effort.label}
          <div className="text-[10px]" style={{ color: 'var(--text-3)' }}>{effort.hint}</div>
        </button>
      ))}
      <div className="px-2 py-2 text-[10px]" style={{ color: 'var(--text-3)' }}>
        Sets the model&rsquo;s thinking budget and the depth of the answer.
        Models that will not take the level are asked again without one, so a
        pick never costs you an answer.
      </div>
    </div>
  )
}

export function AskPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [history, setHistory] = useState<TutorTurn[]>([])
  const [draft, setDraft] = useState('')
  const [source, setSource] = useState<string | null>(null)
  const [model, setModel] = useState<string | null>(() => stored(MODEL_KEY))
  const [effort, setEffort] = useState<Effort>(
    () => (stored(EFFORT_KEY) as Effort | null) ?? 'medium',
  )
  const [menu, setMenu] = useState<'model' | 'effort' | null>(null)
  const [listening, setListening] = useState(false)
  const [micError, setMicError] = useState<string | null>(null)

  const ask = useAsk()
  const endRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const dictation = useRef<Dictation | null>(null)
  // WHAT WAS ALREADY TYPED WHEN THE MICROPHONE STARTED. The recogniser revises
  // its whole transcript as it goes, so the only safe way to combine the two
  // is to keep the typed prefix here and rebuild on every update.
  const typedBefore = useRef('')

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [history, ask.isPending])

  useEffect(() => {
    if (open) inputRef.current?.focus()
  }, [open])

  // Escape closes the popup first, then the panel. A panel that jumps shut
  // when you meant to dismiss a dropdown is one you stop opening.
  useEffect(() => {
    if (!open) return
    function onKey(event: KeyboardEvent) {
      if (event.key !== 'Escape') return
      if (menu) setMenu(null)
      else onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose, menu])

  // THE MICROPHONE MUST NOT OUTLIVE THE PANEL. Closing it while listening
  // would leave the recogniser running and the browser's recording indicator
  // lit, with nothing on screen to explain why.
  useEffect(() => {
    // Guarded so this is a no-op on every close where nothing was listening,
    // which is nearly all of them. The setState that remains is the panel
    // catching up with an external system it just switched off — the one
    // case an effect is for.
    if (open || !dictation.current) return
    dictation.current.stop()
    dictation.current = null
    setListening(false)
  }, [open])

  function toggleMic() {
    setMicError(null)
    if (listening) {
      dictation.current?.stop()
      return
    }
    typedBefore.current = draft ? draft.trimEnd() + ' ' : ''
    const started = listen(
      (text) => setDraft(typedBefore.current + text),
      (message) => { setMicError(message); setListening(false) },
      () => { setListening(false); dictation.current = null; inputRef.current?.focus() },
    )
    if (!started) {
      setMicError('This browser has no speech recognition. Chrome or Edge do.')
      return
    }
    dictation.current = started
    setListening(true)
  }

  function submit(question: string) {
    const text = question.trim()
    if (!text || ask.isPending) return
    dictation.current?.stop()
    const before = history
    setHistory([...before, { role: 'user', content: text }])
    setDraft('')
    setMenu(null)
    ask.mutate(
      // HISTORY AS IT WAS BEFORE THIS QUESTION. The server appends the
      // question itself; sending it in both places would show the model the
      // same sentence twice and invite it to answer the earlier copy.
      { question: text, history: before, effort, ...(model ? { model } : {}) },
      {
        onSuccess: (data) => {
          setHistory([
            ...before,
            { role: 'user', content: text },
            { role: 'assistant', content: data.answer },
          ])
          setSource(
            (data.fell_back ? 'fell back to ' : '') +
            data.provider + ' / ' + data.model +
            ' · spot ' + data.spot + ' · ' + data.session_time,
          )
        },
        onError: () => {
          setHistory(before)
          setDraft(text)
        },
      },
    )
  }

  if (!open) return null

  const chosenEffort = EFFORTS.find((e) => e.id === effort) ?? EFFORTS[1]

  return (
    <>
      {/* Click anywhere on the board to dismiss. */}
      <div
        onClick={onClose}
        className="fixed inset-0 z-40"
        style={{ background: 'rgba(0,0,0,0.35)' }}
        aria-hidden
      />
      <aside
        role="dialog"
        aria-label="Ask about this snapshot"
        className="fixed right-0 top-0 z-50 flex h-full w-full max-w-[440px] flex-col"
        style={{ background: 'var(--bg)', borderLeft: '1px solid var(--border)' }}
      >
        <header
          className="flex items-center justify-between px-4 py-3"
          style={{ borderBottom: '1px solid var(--border)' }}
        >
          <div>
            <div className="text-[13px] font-semibold" style={{ color: 'var(--text)' }}>
              Ask
            </div>
            <div className="text-[11px]" style={{ color: 'var(--text-3)' }}>
              {source ?? 'Reads this snapshot only. No news, no history, no trade advice.'}
            </div>
          </div>
          <div className="flex gap-2">
            {history.length > 0 && (
              <button
                type="button"
                onClick={() => { setHistory([]); setSource(null) }}
                className="rounded-[6px] px-2 py-1 text-[11px]"
                style={{ color: 'var(--text-3)', border: '1px solid var(--border)' }}
              >
                Clear
              </button>
            )}
            <button
              type="button"
              onClick={onClose}
              aria-label="Close"
              className="rounded-[6px] px-2 py-1 text-[13px]"
              style={{ color: 'var(--text-3)', border: '1px solid var(--border)' }}
            >
              &#10005;
            </button>
          </div>
        </header>

        <div className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
          {history.length === 0 && (
            <div className="space-y-2">
              <div className="text-[12px]" style={{ color: 'var(--text-3)' }}>
                Not sure what to ask?
              </div>
              {STARTERS.map((starter) => (
                <button
                  key={starter}
                  type="button"
                  onClick={() => submit(starter)}
                  className="w-full rounded-[8px] px-3 py-2 text-left text-[12px]"
                  style={{
                    background: 'var(--bg-card)',
                    border: '1px solid var(--border)',
                    color: 'var(--text)',
                  }}
                >
                  {starter}
                </button>
              ))}
            </div>
          )}

          {history.map((turn, i) => (
            <Bubble key={String(i) + turn.role} turn={turn} />
          ))}

          {ask.isPending && (
            <div className="text-[12px]" style={{ color: 'var(--text-3)' }}>
              Reading the figures&hellip;
            </div>
          )}

          {ask.isError && (
            <div
              className="rounded-[8px] px-3 py-2 text-[12px]"
              style={{
                background: 'var(--bg-card)',
                border: '1px solid #f0a752',
                color: 'var(--text)',
              }}
            >
              {explainFailure(ask.error).title}
              <div className="mt-1" style={{ color: 'var(--text-3)' }}>
                {explainFailure(ask.error).hint}
              </div>
            </div>
          )}

          <div ref={endRef} />
        </div>

        <div className="px-4 py-3" style={{ borderTop: '1px solid var(--border)' }}>
          <div className="relative mb-2 flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => setMenu(menu === 'model' ? null : 'model')}
              style={CHIP}
              aria-haspopup="listbox"
              aria-expanded={menu === 'model'}
              title="Which model answers first. It is a head start, not a lock — a busy free tier falls through to the next."
            >
              {model ?? 'Auto'} &#9662;
            </button>
            <button
              type="button"
              onClick={() => setMenu(menu === 'effort' ? null : 'effort')}
              style={CHIP}
              aria-haspopup="listbox"
              aria-expanded={menu === 'effort'}
              title={chosenEffort.hint}
            >
              {chosenEffort.label} &#9662;
            </button>
            {dictationSupported() && (
              <button
                type="button"
                onClick={toggleMic}
                aria-pressed={listening}
                aria-label={listening ? 'Stop dictating' : 'Dictate the question'}
                title="Speak your question. The browser does the recognition — the audio does not go through this project's server."
                style={{
                  ...CHIP,
                  background: listening ? '#f05252' : 'var(--bg-card)',
                  color: listening ? '#fff' : 'var(--text)',
                }}
              >
                {listening ? '■ Listening' : '\u{1F3A4} Speak'}
              </button>
            )}

            {menu === 'model' && (
              <ModelPopup
                picked={model}
                onPick={(id) => { setModel(id); remember(MODEL_KEY, id) }}
                onClose={() => setMenu(null)}
              />
            )}
            {menu === 'effort' && (
              <EffortPopup
                picked={effort}
                onPick={(e) => { setEffort(e); remember(EFFORT_KEY, e) }}
                onClose={() => setMenu(null)}
              />
            )}
          </div>

          {micError && (
            <div className="mb-2 text-[11px]" style={{ color: '#f0a752' }}>
              {micError}
            </div>
          )}

          <textarea
            ref={inputRef}
            value={draft}
            rows={2}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              // Enter sends, Shift+Enter breaks the line. A chat box where
              // Enter inserts a newline is one you need the mouse to use.
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                submit(draft)
              }
            }}
            placeholder={listening ? 'Listening…' : 'Ask about what is on the screen…'}
            className="w-full resize-none rounded-[8px] px-3 py-2 text-[13px]"
            style={{
              background: 'var(--bg-card)',
              border: listening ? '1px solid #f05252' : '1px solid var(--border)',
              color: 'var(--text)',
            }}
          />
          <button
            type="button"
            onClick={() => submit(draft)}
            disabled={ask.isPending || draft.trim() === ''}
            className="mt-2 w-full rounded-[8px] px-3 py-2 text-[12px]"
            style={{
              background: draft.trim() === '' ? 'var(--bg-card)' : 'var(--blue)',
              color: draft.trim() === '' ? 'var(--text-3)' : '#fff',
              border: '1px solid var(--border)',
              cursor: ask.isPending || draft.trim() === '' ? 'not-allowed' : 'pointer',
            }}
          >
            {ask.isPending ? 'Reading…' : 'Ask'}
          </button>
        </div>
      </aside>
    </>
  )
}

/** The button that opens it. Fixed bottom-right so it is reachable from every
 *  tab without any of them having to know the panel exists. */
export function AskButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label="Ask about this snapshot"
      className="fixed bottom-5 right-5 z-30 rounded-full px-4 py-3 text-[13px] font-semibold shadow-lg"
      style={{ background: 'var(--blue)', color: '#fff', border: 'none', cursor: 'pointer' }}
    >
      Ask
    </button>
  )
}
