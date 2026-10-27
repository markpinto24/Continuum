import { Activity, Loader2, Table2 } from 'lucide-react'
import { type PointerEvent, useState } from 'react'

import { useResource } from '@/hooks/use-resource'
import { api } from '@/lib/api'
import type { ActivityDay, ActivityReport } from '@/lib/types'
import { cn } from '@/lib/utils'

const RANGES = [7, 30, 90] as const
type Range = (typeof RANGES)[number]

/** The four daily counts, each drawn as its own titled strip. */
const SERIES: { key: 'remembered' | 'escalated' | 'auto_retired' | 'decided'; label: string }[] = [
  { key: 'remembered', label: 'Taken in' },
  { key: 'escalated', label: 'Asked you' },
  { key: 'auto_retired', label: 'Retired on its own' },
  { key: 'decided', label: 'Decided by you' },
]

const STRIP_HEIGHT = 26
const JUDGE_HEIGHT = 64
const WIDTH = 300 // viewBox units; the SVG scales to the panel

/**
 * The resolver over time, in this graph: what it took in, how often it asked
 * instead of deciding, what it retired on its own, and what you decided — plus
 * how sure the judge was, against the gate.
 *
 * Small multiples, one series each, all in the accent: every strip is titled, so
 * colour carries no identity and no palette has to be told apart (validated:
 * the status colours fail as a categorical set on this surface). Hovering any
 * strip reads out that whole day.
 */
export function ActivityPanel({ version }: { version?: unknown }) {
  const [range, setRange] = useState<Range>(30)
  const [table, setTable] = useState(false)
  const [hover, setHover] = useState<number | null>(null)
  const resource = useResource(() => api.feedbackActivity(range), [range, version])
  const data = resource.data

  return (
    <section className="mt-3 space-y-2.5 rounded-md border border-border bg-surface/60 px-3 py-2.5">
      <div className="flex items-center gap-1.5">
        <h3 className="flex items-center gap-1.5 text-[11px] font-medium text-foreground">
          <Activity className="size-3.5 text-accent" />
          Activity
        </h3>
        {resource.loading && <Loader2 className="size-3 animate-spin text-muted" />}
        <div className="ml-auto flex items-center gap-1.5">
          <div className="flex rounded border border-border text-[10px]" role="group" aria-label="Range">
            {RANGES.map((days) => (
              <button
                key={days}
                type="button"
                aria-pressed={range === days}
                onClick={() => setRange(days)}
                className={cn(
                  'px-1.5 py-0.5',
                  range === days ? 'bg-accent/15 text-accent' : 'text-muted hover:text-foreground',
                )}
              >
                {days}d
              </button>
            ))}
          </div>
          <button
            type="button"
            aria-pressed={table}
            aria-label="Show as a table"
            onClick={() => setTable((t) => !t)}
            className={cn('rounded p-0.5', table ? 'text-accent' : 'text-muted hover:text-foreground')}
          >
            <Table2 className="size-3.5" />
          </button>
        </div>
      </div>

      {resource.error && <p className="text-[11px] text-danger">{resource.error}</p>}
      {data && (
        <>
          <Tiles report={data} />
          {table ? (
            <ActivityTable days={data.days} />
          ) : (
            <Charts report={data} hover={hover} onHover={setHover} />
          )}
        </>
      )}
    </section>
  )
}

function Tiles({ report }: { report: ActivityReport }) {
  const rate =
    report.escalation_rate === null ? '—' : `${Math.round(report.escalation_rate * 100)}%`
  const tiles = [
    { value: rate, label: 'asked you', hint: 'of what it took in' },
    { value: String(report.auto_retired), label: 'retired', hint: 'on its own' },
    { value: String(report.decided), label: 'decided', hint: 'by you' },
  ]
  return (
    <dl className="grid grid-cols-3 gap-1.5">
      {tiles.map((tile) => (
        <div key={tile.label} className="rounded border border-border px-2 py-1.5">
          <dd className="text-base font-semibold tabular-nums text-foreground">{tile.value}</dd>
          <dt className="text-[10px] leading-tight text-muted">
            {tile.label} <span className="text-muted/70">{tile.hint}</span>
          </dt>
        </div>
      ))}
    </dl>
  )
}

function Charts({
  report,
  hover,
  onHover,
}: {
  report: ActivityReport
  hover: number | null
  onHover: (index: number | null) => void
}) {
  const { days } = report
  const step = WIDTH / days.length
  // One pointer handler for every strip: the column under the pointer, for all.
  const track = (event: PointerEvent<SVGSVGElement>) => {
    const box = event.currentTarget.getBoundingClientRect()
    if (box.width === 0) return // not laid out (collapsed panel)
    const index = Math.floor(((event.clientX - box.left) / box.width) * days.length)
    if (!Number.isFinite(index)) return
    onHover(Math.max(0, Math.min(days.length - 1, index)))
  }
  const day = hover === null ? null : days[hover]

  return (
    <div className="space-y-1.5" onPointerLeave={() => onHover(null)}>
      <p className="h-4 truncate text-[10px] tabular-nums text-muted" aria-live="polite">
        {day ? readout(day) : `Last ${days.length} days — hover for a day`}
      </p>
      {SERIES.map(({ key, label }) => {
        const max = Math.max(1, ...days.map((d) => d[key]))
        const total = days.reduce((sum, d) => sum + d[key], 0)
        return (
          <figure key={key} className="space-y-0.5">
            <figcaption className="flex justify-between text-[10px] text-muted">
              <span className="text-foreground/90">{label}</span>
              <span className="tabular-nums">{total} total · max {max}/day</span>
            </figcaption>
            <svg
              viewBox={`0 0 ${WIDTH} ${STRIP_HEIGHT}`}
              preserveAspectRatio="none"
              className="block h-[26px] w-full"
              role="img"
              aria-label={`${label}: ${total} over ${days.length} days`}
              onPointerMove={track}
            >
              {hover !== null && (
                <rect x={hover * step} width={step} height={STRIP_HEIGHT} className="fill-foreground/5" />
              )}
              <line x1={0} x2={WIDTH} y1={STRIP_HEIGHT - 0.5} y2={STRIP_HEIGHT - 0.5} className="stroke-border" />
              {days.map((d, i) => {
                const h = (d[key] / max) * (STRIP_HEIGHT - 2)
                return h > 0 ? (
                  <rect
                    key={d.day}
                    // A gap between neighbours, so a run of days reads as days.
                    x={i * step + Math.min(1, step * 0.15)}
                    width={Math.max(0.6, step - Math.min(2, step * 0.3))}
                    y={STRIP_HEIGHT - 1 - h}
                    height={h}
                    rx={Math.min(1.5, step / 4)}
                    className={cn('fill-accent', hover !== null && hover !== i && 'opacity-60')}
                  />
                ) : null
              })}
            </svg>
          </figure>
        )
      })}
      <JudgeConfidence report={report} hover={hover} onMove={track} />
    </div>
  )
}

/**
 * How close it came to retiring on its own: per day, the median confidence of the
 * supersedes the gate sent to you instead — dots below the dashed gate line.
 */
function JudgeConfidence({
  report,
  hover,
  onMove,
}: {
  report: ActivityReport
  hover: number | null
  onMove: (event: PointerEvent<SVGSVGElement>) => void
}) {
  const { days, gate } = report
  const step = WIDTH / days.length
  const y = (p: number) => 4 + (1 - p) * (JUDGE_HEIGHT - 8)
  const judged = days.filter((d) => d.judge_confidence !== null).length
  return (
    <figure className="space-y-0.5 pt-1">
      <figcaption className="flex justify-between text-[10px] text-muted">
        <span className="text-foreground/90">Near misses: replacements held for you</span>
        <span>{judged ? `${judged} days` : 'none yet'}</span>
      </figcaption>
      <div className="relative">
        <svg
          viewBox={`0 0 ${WIDTH} ${JUDGE_HEIGHT}`}
          preserveAspectRatio="none"
          className="block h-16 w-full"
          role="img"
          aria-label={`Median judge confidence of replacements the gate held for you, per day; gate at ${gate.toFixed(2)}`}
          onPointerMove={onMove}
        >
          {hover !== null && (
            <rect x={hover * step} width={step} height={JUDGE_HEIGHT} className="fill-foreground/5" />
          )}
          <line
            x1={0}
            x2={WIDTH}
            y1={y(gate)}
            y2={y(gate)}
            strokeDasharray="3 3"
            vectorEffect="non-scaling-stroke"
            className="stroke-muted/60"
          />
        </svg>
        {/* Dots as HTML over the stretched SVG, so they stay round at any width. */}
        {days.map((d, i) =>
          d.judge_confidence === null ? null : (
            <span
              key={d.day}
              className={cn(
                'absolute size-2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-accent ring-2 ring-surface',
                hover !== null && hover !== i && 'opacity-60',
              )}
              style={{
                left: `${((i + 0.5) / days.length) * 100}%`,
                top: `${(y(d.judge_confidence) / JUDGE_HEIGHT) * 100}%`,
              }}
            />
          ),
        )}
        <span
          className="pointer-events-none absolute right-0 -translate-y-full text-[9px] text-muted"
          style={{ top: `${(y(gate) / JUDGE_HEIGHT) * 100}%` }}
        >
          gate {gate.toFixed(2)}
        </span>
      </div>
    </figure>
  )
}

function ActivityTable({ days }: { days: ActivityDay[] }) {
  const shown = days.filter(
    (d) => d.remembered || d.escalated || d.auto_retired || d.decided,
  )
  if (shown.length === 0) {
    return <p className="text-[11px] text-muted">Nothing happened in this range.</p>
  }
  return (
    <div className="scrollbar-slim max-h-48 overflow-y-auto">
      <table className="w-full text-[10px] tabular-nums">
        <thead className="text-left text-muted">
          <tr>
            <th className="font-normal">Day</th>
            {SERIES.map((s) => (
              <th key={s.key} className="text-right font-normal">
                {s.label}
              </th>
            ))}
            <th className="text-right font-normal">Judge</th>
          </tr>
        </thead>
        <tbody className="text-foreground/90">
          {[...shown].reverse().map((d) => (
            <tr key={d.day} className="border-t border-border/60">
              <td>{formatDay(d.day)}</td>
              {SERIES.map((s) => (
                <td key={s.key} className="text-right">
                  {d[s.key]}
                </td>
              ))}
              <td className="text-right">{d.judge_confidence?.toFixed(2) ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function readout(day: ActivityDay): string {
  const parts = [
    `${day.remembered} taken in`,
    `${day.escalated} asked`,
    `${day.auto_retired} retired on its own`,
    `${day.decided} decided`,
  ]
  if (day.decided_by_agent) parts.push(`${day.decided_by_agent} via an agent`)
  if (day.judge_confidence !== null) parts.push(`judge ${day.judge_confidence.toFixed(2)}`)
  return `${formatDay(day.day)} · ${parts.join(' · ')}`
}

function formatDay(iso: string): string {
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString(undefined, {
    day: 'numeric',
    month: 'short',
    timeZone: 'UTC',
  })
}
