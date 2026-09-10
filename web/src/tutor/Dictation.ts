/**
 * Dictation — the microphone, using the browser's own speech recognition.
 *
 * NO KEY, NO SERVER, NO AUDIO LEAVING THE PAGE THROUGH US. This is the Web
 * Speech API, which Chrome and Edge implement by streaming to Google's
 * recogniser and handing back text. That is worth knowing and is said on the
 * button's tooltip: the audio does go somewhere, just not to this project's
 * server and not through any key of ours. Firefox and Safari do not implement
 * it at all, which is why `supported()` exists and why the button is simply
 * absent rather than present and broken.
 *
 * INTERIM RESULTS ARE SHOWN AS THEY ARRIVE. Dictating a whole question into a
 * silent box and finding out at the end that the first clause was misheard is
 * the failure mode of every push-to-talk field; watching the words appear lets
 * you stop and start again after three seconds instead of thirty.
 *
 * TYPED, LOCALLY. The API is not in TypeScript's DOM library, so the shapes
 * below are the parts of the spec this file actually touches. A broad `any`
 * would compile just as well and would let a typo through silently.
 */

interface SpeechAlternative { transcript: string }
interface SpeechResult {
  readonly length: number
  isFinal: boolean
  [index: number]: SpeechAlternative
}
interface SpeechResultList {
  readonly length: number
  [index: number]: SpeechResult
}
interface SpeechEvent extends Event {
  resultIndex: number
  results: SpeechResultList
}
interface SpeechErrorEvent extends Event {
  error: string
}

interface SpeechRecognition extends EventTarget {
  lang: string
  continuous: boolean
  interimResults: boolean
  start(): void
  stop(): void
  abort(): void
  onresult: ((event: SpeechEvent) => void) | null
  onerror: ((event: SpeechErrorEvent) => void) | null
  onend: (() => void) | null
}

type SpeechRecognitionCtor = new () => SpeechRecognition

function constructor(): SpeechRecognitionCtor | null {
  const w = window as unknown as {
    SpeechRecognition?: SpeechRecognitionCtor
    webkitSpeechRecognition?: SpeechRecognitionCtor
  }
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null
}

export function supported(): boolean {
  return constructor() !== null
}

/** Why a failed attempt failed, in words a reader can act on.
 *
 *  `not-allowed` is by far the most common and is the one that looks like a
 *  broken button: the browser refused the microphone and told the page in a
 *  callback nobody surfaces, so the button lights up and nothing is heard. */
function explain(error: string): string {
  if (error === 'not-allowed' || error === 'service-not-allowed') {
    return 'The browser blocked the microphone. Allow it for this page and try again.'
  }
  if (error === 'no-speech') return 'Nothing was heard.'
  if (error === 'audio-capture') return 'No microphone was found.'
  if (error === 'network') return 'The speech service could not be reached.'
  return 'Dictation stopped: ' + error
}

export interface Dictation {
  stop: () => void
}

/**
 * Start listening. `onText` receives the transcript so far on every update —
 * it REPLACES rather than appends, because the recogniser revises what it
 * already gave you as later words disambiguate earlier ones.
 */
export function listen(
  onText: (text: string, final: boolean) => void,
  onError: (message: string) => void,
  onEnd: () => void,
): Dictation | null {
  const Ctor = constructor()
  if (!Ctor) return null

  const recogniser = new Ctor()
  recogniser.lang = navigator.language || 'en-US'
  // CONTINUOUS, because a question about vanna takes longer to say than the
  // default single-utterance timeout allows, and being cut off mid-sentence
  // reads as the button having failed.
  recogniser.continuous = true
  recogniser.interimResults = true

  recogniser.onresult = (event) => {
    let text = ''
    let final = false
    for (let i = 0; i < event.results.length; i += 1) {
      const result = event.results[i]
      text += result[0].transcript
      if (result.isFinal) final = true
    }
    onText(text.trim(), final)
  }
  recogniser.onerror = (event) => {
    // `aborted` is what stop() itself raises. Reporting it would put an error
    // on screen every time dictation ended normally.
    if (event.error !== 'aborted') onError(explain(event.error))
  }
  recogniser.onend = onEnd

  try {
    recogniser.start()
  } catch {
    // Already running, in practice. Nothing useful to say and nothing broken.
    return { stop: () => recogniser.abort() }
  }
  return { stop: () => recogniser.stop() }
}
