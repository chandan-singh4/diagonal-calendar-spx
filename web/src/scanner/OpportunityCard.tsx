/**
 * One Mission Control opportunity card — the React counterpart of
 * `views/scanner.py:_render_mc_section`.
 *
 * WHAT THIS FILE IS ALLOWED TO DO: choose words, colours and layout. It reads
 * `duration_label`, `eta_label` and `last_seen_ago` rather than the raw
 * timedelta and float beside them, because turning 132.4 minutes into
 * "~2.2 hr" is a rounding rule and rounding rules live in core/format.py.
 * `.toFixed()` below is presentation of a number already decided, which is
 * the line this file stays on the right side of.
 */
import type { Card } from '../api/types'

/** The two states a card can be in. They differ in more than a badge: a live
 *  card shows how long it has been live, a past one shows when it last was,
 *  and the gap they show is a different number (current vs peak). */
type Badge = 'LIVE' | 'PAST' | 'OUTSIDE WINDOW' | null

function badgeFor(card: Card, showLive: boolean): Badge {
  if (!showLive) return null
  if (card.is_live) return 'LIVE'
  return card.outside_lookback ? 'OUTSIDE WINDOW' : 'PAST'
}

interface MetricProps {
  label: string
  value: string
  tone?: 'green' | 'stale' | 'plain'
}

function Metric({ label, value, tone = 'plain' }: MetricProps) {
  const colour =
    tone === 'green' ? 'var(--green)' : tone === 'stale' ? 'var(--text-2)' : 'var(--text)'
  return (
    <div className="flex items-baseline justify-between gap-3 py-[2px]">
      <span className="text-[11px] tracking-wide" style={{ color: 'var(--text-2)' }}>
        {label}
      </span>
      <span className="mono text-[13px] font-semibold" style={{ color: colour }}>
        {value}
      </span>
    </div>
  )
}

export interface OpportunityCardProps {
  card: Card
  /** 1-based, shown as #1, #2 … exactly as the page numbers them. */
  rank: number
  /** Non-ATM grid only; the Likely Next grid has no live/past distinction. */
  showLiveBadge?: boolean
  /** Suppressed on Likely Next, which by definition has not started yet. */
  showDuration?: boolean
  /** True when this pair is in `/mission/new`'s `new_keys`. Decided by the
   *  caller from a set-membership test on the server-built `key`. */
  isNew?: boolean
  /** Open Calendar Edge on this pair. THE WHOLE CARD IS THE CONTROL — see
   *  the note on the element below. */
  onOpen: () => void
}

export function OpportunityCard({
  card,
  rank,
  showLiveBadge = false,
  showDuration = true,
  isNew = false,
  onOpen,
}: OpportunityCardProps) {
  const badge = badgeFor(card, showLiveBadge)
  const live = card.is_live ?? true
  // "Peak Gap" when the card is showing history rather than the present. The
  // label has to change with the number or the card claims a stale figure is
  // the current one — the same fault BUG-038 was about, in miniature.
  const gapLabel = live ? 'Gap' : 'Peak Gap'

  // THE WHOLE CARD IS THE BUTTON, and a real <button> rather than a div with
  // a click handler — so it takes keyboard focus, answers Enter and Space,
  // and announces itself to a screen reader as something that does
  // something. A card that is only clickable by mouse is a card half the
  // ways into this app cannot reach.
  return (
    <button
      type="button"
      onClick={onOpen}
      title="Open this pair in Calendar Edge"
      className="w-full rounded-[10px] border p-3 text-left transition-colors"
      style={{
        background: 'var(--bg-card)',
        borderColor: live && showLiveBadge ? 'var(--border-green)' : 'var(--border)',
        boxShadow: live && showLiveBadge ? 'var(--glow-green)' : 'var(--shadow)',
        cursor: 'pointer',
      }}
    >
      <header className="mb-2 flex items-center gap-2">
        <span className="mono text-[11px]" style={{ color: 'var(--text-3)' }}>
          #{rank}
        </span>
        {badge && (
          <span
            className="rounded-[4px] px-[6px] py-[1px] text-[9px] font-bold tracking-widest"
            style={
              badge === 'LIVE'
                ? { background: 'rgba(16,212,163,.14)', color: 'var(--green)' }
                : { background: 'rgba(109,143,168,.12)', color: 'var(--text-2)' }
            }
          >
            {badge}
          </span>
        )}
        {isNew && (
          <span
            className="rounded-[4px] px-[6px] py-[1px] text-[9px] font-bold tracking-widest"
            style={{ background: 'rgba(91,156,255,.16)', color: 'var(--blue)' }}
          >
            NEW
          </span>
        )}
      </header>

      <div className="mono text-[17px] font-semibold" style={{ color: 'var(--text)' }}>
        {card.put_strike.toFixed(0)}P / {card.call_strike.toFixed(0)}C
      </div>
      <div className="mt-[2px] mb-2 text-[11px] leading-snug" style={{ color: 'var(--text-2)' }}>
        {card.front_label} → {card.back_label}
      </div>

      <div style={{ borderTop: '1px solid var(--border)' }} className="pt-2">
        <Metric
          label={gapLabel}
          // The arrow rides on the number it describes, not on the sparkline —
          // moved there on 2026-07-30 and kept here on purpose.
          value={`+${card.gap.toFixed(2)}${card.trend_up ? ' ↑' : ''}`}
          tone={live ? 'green' : 'stale'}
        />
        {showDuration && live && <Metric label="Active" value={card.duration_label} />}
        {showLiveBadge && !live && (
          <Metric label="Last Crossed" value={card.last_seen_ago ?? '—'} />
        )}
        {showLiveBadge && card.hit_count !== undefined && (
          <Metric label="Seen" value={`${card.hit_count}×`} />
        )}
        {/* An absent IV ratio is a blank, never a zero — a pair with no ratio
            and a pair with a ratio of 0.0000 are different states, and only
            one of them is possible. */}
        <Metric
          label="IV Ratio"
          value={card.iv_ratio === null ? '—' : card.iv_ratio.toFixed(4)}
        />
      </div>

      {card.eta_minutes !== null && (
        <div className="mt-2 text-[11px]" style={{ color: 'var(--amber)' }}>
          ETA {card.eta_label}
        </div>
      )}
    </button>
  )
}
