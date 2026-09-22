import type { IngestResponse } from './types'

/**
 * One line saying what a chat turn did to the belief graph.
 *
 * Silence is the thing to avoid. The old label only appeared when something
 * was extracted, so a turn that stored nothing looked exactly like a turn
 * where memory was switched off — and "why didn't it remember that?" could
 * only be answered by reading the server logs.
 *
 * It also used to count `extracted`, which includes facts that turned out to
 * be duplicates. "Remembered 1" for a fact that only confirmed an existing
 * memory overstated what happened; this reports each outcome separately.
 *
 * Returns null when there is no write-back to describe (memory disabled for
 * the turn, or the write-back failed after the answer was delivered).
 */
export function describeWriteBack(remembered: IngestResponse | null): string | null {
  if (!remembered) return null

  if (remembered.extracted === 0) {
    return 'Nothing stored — no durable fact found in this turn'
  }

  const parts: string[] = []
  const created = remembered.created.length
  if (created > 0) parts.push(`stored ${created} new ${created === 1 ? 'memory' : 'memories'}`)
  if (remembered.reinforced.length > 0) parts.push(`confirmed ${remembered.reinforced.length}`)
  if (remembered.superseded.length > 0) parts.push(`superseded ${remembered.superseded.length}`)
  const disputes = remembered.conflicts_raised.length
  if (disputes > 0) parts.push(`raised ${disputes} ${disputes === 1 ? 'dispute' : 'disputes'}`)

  if (parts.length === 0) return 'Nothing changed in the graph'
  const line = parts.join(' · ')
  return line.charAt(0).toUpperCase() + line.slice(1)
}
