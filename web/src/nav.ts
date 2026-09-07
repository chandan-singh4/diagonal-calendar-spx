/**
 * Which tab is showing, and what it is scoped to — held in the URL.
 *
 * `#edge?front=2026-09-04&back=2026-09-08&put=7620&call=7720` is the whole
 * of it. Not a router: six tabs and one parameter set do not need one, and a
 * router would be the larger of the two things to keep in step.
 *
 * WHY THE SELECTION LIVES IN THE URL AND NOT IN A CONTEXT. The Streamlit
 * drill-down stages the four values in `st.session_state` and reruns
 * (`views/scanner.py`), which is invisible and unshareable — you cannot send
 * someone the chart you are looking at. Here the same jump produces an
 * address: a reload comes back to the same pair, and the link can be pasted
 * into a message. That is a genuine improvement on the page, not a
 * difference from it.
 *
 * THE EXPIRIES ARE DISPLAY KEYS, NOT DATES. The third Friday appears twice —
 * "2026-09-18" for the p.m. contract and "2026-09-18 (AM)" for the a.m. one
 * — and they are different options (ADR-046, ADR-047). They are carried
 * through URL-encoded and never parsed back into dates.
 */

export interface EdgeSelection {
  front: string
  back: string
  putStrike: number
  callStrike: number
}

export interface Route {
  tab: string
  selection: EdgeSelection | null
}

/** The address for one pair on one tab. */
export function routeHash(tab: string, selection: EdgeSelection | null): string {
  if (!selection) return `#${tab}`
  const query = new URLSearchParams({
    front: selection.front,
    back: selection.back,
    put: String(selection.putStrike),
    call: String(selection.callStrike),
  })
  return `#${tab}?${query}`
}

/** Read the address back.
 *
 *  A PARTIAL SELECTION IS NO SELECTION. All four values are required: a pair
 *  missing its back expiry would silently fall back to the server's default
 *  for that leg, and the tab would then be showing a different pair from the
 *  one the link names — while looking entirely correct. */
export function parseRoute(hash: string, isKnownTab: (id: string) => boolean): Route {
  const raw = hash.replace(/^#/, '')
  const [tab, query] = raw.split('?')
  if (!isKnownTab(tab)) return { tab: 'scanner', selection: null }

  const params = new URLSearchParams(query ?? '')
  const front = params.get('front')
  const back = params.get('back')
  const put = Number(params.get('put'))
  const call = Number(params.get('call'))
  if (!front || !back || !Number.isFinite(put) || !Number.isFinite(call)) {
    return { tab, selection: null }
  }
  return { tab, selection: { front, back, putStrike: put, callStrike: call } }
}
