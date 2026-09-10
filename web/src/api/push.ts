/**
 * push.ts — the browser end of /ws/snapshot.
 *
 * WHY THIS EXISTS. The API has published a push channel since M4.4: one
 * poller in the server watching `max(snapshot_id)` and fanning the news out,
 * precisely so clients would not each poll on their own. Nothing in the web
 * app ever connected to it. The result was a dashboard that looked live and
 * was not — the header strip refreshed (it carries its own `refetchInterval`)
 * so the screen could sit there reporting that data was two minutes old while
 * every chart beside it still drew the snapshot from when the tab was opened.
 *
 * WHY STALE DID NOT MEAN REFETCHED. Every other query runs on `SHARED`, whose
 * `staleTime` of 30s marks an answer as out of date but does not go and get a
 * new one. React Query refetches a stale query when something TRIGGERS it —
 * a mount, a window focus, a reconnect. A screen that is being watched rather
 * than clicked fires none of those, so the data aged and nothing asked for
 * more. That is the bug this closes.
 *
 * WHY NOT JUST POLL ON A TIMER. A `refetchInterval` on every query would put
 * the load back on the client and, worse, would refetch on a clock unrelated
 * to when data actually lands — sometimes twice for one snapshot, sometimes
 * arriving a full interval late. The server already knows the exact moment a
 * snapshot completes, and it knows it from the same `max(snapshot_id)` that
 * api/cache.py keys on, so a refetch triggered by the push cannot race the
 * cache and get the previous snapshot's answer back.
 *
 * THE TIMER THAT REMAINS IS A FALLBACK, not the mechanism — see below.
 */
import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'

/**
 * Query keys NOT invalidated when a snapshot lands.
 *
 * The model roster is a capability list, not market data: it changes when a
 * provider's line-up changes, which is not something a new snapshot implies.
 * Invalidating it would throw away a 15-minute cache several times an hour to
 * re-fetch an identical answer.
 */
const NOT_MARKET_DATA = new Set(['tutor-models'])

/**
 * How long to wait before reconnecting, growing on repeated failure.
 *
 * BACKED OFF because the common reason the socket cannot be reached is that
 * the API process is down — during a restart, say. A client retrying every
 * second logs a screenful of failures and keeps the machine busy for as long
 * as the server is away; one that gives up entirely needs a page reload at
 * the exact moment the user is least inclined to trust the page. Growing from
 * one second to thirty is fast enough that a restart is invisible and slow
 * enough that an absence is quiet.
 */
const RETRY_MS = [1_000, 2_000, 5_000, 10_000, 30_000]

/**
 * A backstop refetch, used ONLY while the socket is down.
 *
 * Ninety seconds is deliberately slower than the collector's cadence: this is
 * not meant to replace the push, only to keep a disconnected dashboard from
 * going indefinitely stale without saying so. When the socket is up this
 * timer does not run at all.
 */
const FALLBACK_MS = 90_000

function socketUrl(): string {
  // Derived from the page, not hardcoded, so the same bundle works on
  // localhost and over the LAN/Tailscale address a phone uses. The path goes
  // through the same `/api` prefix as every other call, which is what keeps
  // the token attachment and the origin in one place (see vite.config.ts).
  const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${scheme}//${window.location.host}/api/ws/snapshot`
}

/**
 * Connect to the push channel and refetch the board when a snapshot lands.
 *
 * Returns whether the channel is currently connected, so the header can say
 * so. A dashboard that has quietly lost its live feed looks exactly like a
 * quiet market, and the whole point of this file is to stop the screen
 * implying it is current when it is not.
 */
export function useSnapshotPush(): { connected: boolean; lastSnapshotId: number | null } {
  const queries = useQueryClient()
  const [connected, setConnected] = useState(false)
  const [lastSnapshotId, setLastSnapshotId] = useState<number | null>(null)

  // Held in refs so the effect below can run ONCE for the life of the app.
  // Putting `connected` in its dependency list would tear the socket down and
  // rebuild it on every state change it causes — a reconnect loop driven by
  // its own success.
  const attempt = useRef(0)
  const timer = useRef<number | undefined>(undefined)
  const closing = useRef(false)

  useEffect(() => {
    let socket: WebSocket | null = null
    let fallback: number | undefined

    const refetchAll = () => {
      void queries.invalidateQueries({
        predicate: (query) => !NOT_MARKET_DATA.has(String(query.queryKey[0])),
      })
    }

    const startFallback = () => {
      if (fallback === undefined) {
        fallback = window.setInterval(refetchAll, FALLBACK_MS)
      }
    }
    const stopFallback = () => {
      if (fallback !== undefined) {
        window.clearInterval(fallback)
        fallback = undefined
      }
    }

    const connect = () => {
      if (closing.current) return
      socket = new WebSocket(socketUrl())

      socket.onopen = () => {
        attempt.current = 0
        setConnected(true)
        stopFallback()
      }

      socket.onmessage = (event) => {
        let message: { event?: string; snapshot_id?: number | null }
        try {
          message = JSON.parse(String(event.data)) as typeof message
        } catch {
          // A message we cannot parse is not a reason to drop the channel.
          return
        }
        if (typeof message.snapshot_id === 'number') {
          setLastSnapshotId(message.snapshot_id)
        }
        // ONLY `snapshot` REFETCHES. The server sends `current` on connect to
        // say where things stand; treating that as news would refetch the
        // whole board on every reconnect, which on a flaky link is a refetch
        // storm caused by the reconnect rather than by any new data.
        if (message.event === 'snapshot') {
          refetchAll()
        }
      }

      const retry = () => {
        setConnected(false)
        startFallback()
        if (closing.current) return
        const wait = RETRY_MS[Math.min(attempt.current, RETRY_MS.length - 1)]
        attempt.current += 1
        timer.current = window.setTimeout(connect, wait)
      }

      socket.onclose = retry
      // `onerror` is followed by `onclose` in every browser, so reconnecting
      // here as well would schedule two attempts for one failure.
      socket.onerror = () => socket?.close()
    }

    closing.current = false
    connect()

    return () => {
      closing.current = true
      stopFallback()
      if (timer.current !== undefined) window.clearTimeout(timer.current)
      // Cleared first: otherwise our own teardown fires `onclose` and
      // schedules a reconnect to a page that is going away.
      if (socket) {
        socket.onclose = null
        socket.onerror = null
        socket.close()
      }
    }
  }, [queries])

  return { connected, lastSnapshotId }
}
