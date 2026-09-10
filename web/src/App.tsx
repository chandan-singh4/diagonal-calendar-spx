/**
 * The app shell. Four tabs so far — Scanner, Gamma Exposure, Calendar Edge
 * and Strike Detail — and the strip names the two unbuilt ones too, disabled,
 * because a rebuild that hides its own remaining scope is easy to mistake for
 * a finished one.
 *
 * Two of the three are PARTIAL, and are marked ready because what they draw
 * is right, not because they are complete. Gamma Exposure has its strike
 * panels but not its time panels, 0DTE board, dealer structure, net flow or
 * replay. Calendar Edge is read-only: the Streamlit tab writes entry locks
 * and this one cannot. Both say so on screen; web/README.md has the list.
 */
import { useEffect, useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import { useSnapshotPush } from './api/push'
import { EdgeTab } from './edge/EdgeTab'
import { GammaTab } from './gamma/GammaTab'
import { type EdgeSelection, parseRoute, routeHash } from './nav'
import { ScannerTab } from './scanner/ScannerTab'
import { HeaderBar } from './shell/HeaderBar'
import { StrikeTab } from './strike/StrikeTab'
import { AskButton, AskPanel } from './tutor/AskPanel'
import './theme.css'

const queryClient = new QueryClient()

/**
 * Holds the push connection open for the life of the app.
 *
 * A COMPONENT RATHER THAN A HOOK CALL IN `App`, for one reason: the hook
 * needs the query client, which is only available BELOW the provider, and
 * `App` is what renders the provider. Rendering nothing is the point — it
 * exists to run an effect, not to draw.
 */
function LiveData() {
  useSnapshotPush()
  return null
}

const TABS = [
  { id: 'scanner', label: 'Scanner', ready: true },
  { id: 'gex', label: 'Gamma Exposure', ready: true },
  { id: 'edge', label: 'Calendar Edge', ready: true },
  { id: 'strike', label: 'Strike Detail', ready: true },
  { id: 'research', label: 'Research', ready: false },
  { id: 'entry', label: 'Entry Analysis', ready: false },
] as const

function TabStrip({ active, onSelect }: { active: string; onSelect: (id: string) => void }) {
  return (
    <nav
      className="flex flex-wrap gap-1 px-5 pt-3"
      style={{ borderBottom: '1px solid var(--border)' }}
      aria-label="Dashboard sections"
    >
      {TABS.map((tab) => (
        <button
          key={tab.id}
          type="button"
          disabled={!tab.ready}
          onClick={() => onSelect(tab.id)}
          aria-current={tab.id === active ? 'page' : undefined}
          className="rounded-t-[6px] px-3 py-[6px] text-[12px]"
          style={{
            color: tab.ready ? 'var(--text)' : 'var(--text-3)',
            background: tab.id === active ? 'var(--bg-card)' : 'transparent',
            borderBottom:
              tab.id === active ? '2px solid var(--blue)' : '2px solid transparent',
            cursor: tab.ready ? 'pointer' : 'not-allowed',
          }}
          title={tab.ready ? undefined : 'Still served by the Streamlit dashboard'}
        >
          {tab.label}
        </button>
      ))}
    </nav>
  )
}

function isKnownTab(id: string): boolean {
  return TABS.some((tab) => tab.id === id && tab.ready)
}

/** The tab named in the URL, and the pair it is scoped to. Read at startup
 *  and written on every move, so a tab can be linked to and a reload comes
 *  back to what you were looking at. See src/nav.ts. */
function routeFromHash() {
  return parseRoute(window.location.hash, isKnownTab)
}

export default function App() {
  const [route, setRoute] = useState(routeFromHash)
  // NOT IN THE HASH. The panel is a lens on whatever tab is open, not a
  // place you can be; putting it in the address would make a shared link
  // reopen someone else's half-finished conversation.
  const [asking, setAsking] = useState(false)
  const active = route.tab

  function select(id: string) {
    // SWITCHING TABS BY HAND DROPS THE SELECTION. Carrying it would mean
    // clicking "Calendar Edge" silently reopened a pair chosen minutes ago
    // on another screen; the tab strip is a request for the tab, not for a
    // pair. The drill-down below is the thing that carries one.
    setRoute({ tab: id, selection: null })
    window.location.hash = id
  }

  /** One click from a Scanner card or row: open Calendar Edge on that pair.
   *
   *  NO CONFIRMATION STEP, and that is Chandan's call (2026-09-06). The
   *  Streamlit page needs one — a row has to be selected, then "View Chart"
   *  pressed — because Streamlit reruns top to bottom and the Controls Bar
   *  has already drawn by the time the click is seen, so the values are
   *  staged under `pending_` keys for the NEXT run. That is a constraint of
   *  the framework, not a decision about what a click should mean. Here the
   *  click can just do the only thing it was ever for. */
  function openEdge(selection: EdgeSelection) {
    setRoute({ tab: 'edge', selection })
    window.location.hash = routeHash('edge', selection)
  }

  // The back button. Without this, a drill-down is a one-way trip: the hash
  // changes, the browser records it, and going back rewrites the address
  // while the page carries on showing the tab you were on.
  useEffect(() => {
    function onHashChange() {
      setRoute(routeFromHash())
    }
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  // The browser tab says which dashboard tab you are on. It said "Scanner"
  // from index.html whichever one was showing, which is the kind of label
  // that is wrong for months because nobody reads their own title bar.
  useEffect(() => {
    const tab = TABS.find((t) => t.id === active)
    document.title = tab ? `SPX Diagonal — ${tab.label}` : 'SPX Diagonal'
  }, [active])

  return (
    <QueryClientProvider client={queryClient}>
      <LiveData />
      <HeaderBar />
      <TabStrip active={active} onSelect={select} />
      {active === 'gex' ? (
        <GammaTab />
      ) : active === 'edge' ? (
        // Keyed by the selection so arriving from a different pair REMOUNTS
        // the tab. Its four pickers seed from the selection on first render;
        // without the key a second drill-down would change the address and
        // leave the previous pair on screen.
        <EdgeTab key={routeHash('edge', route.selection)} initial={route.selection} />
      ) : active === 'strike' ? (
        <StrikeTab key={routeHash('strike', route.selection)} initial={route.selection} />
      ) : (
        <ScannerTab onOpenEdge={openEdge} />
      )}
      <AskButton onClick={() => setAsking(true)} />
      <AskPanel open={asking} onClose={() => setAsking(false)} />
    </QueryClientProvider>
  )
}
