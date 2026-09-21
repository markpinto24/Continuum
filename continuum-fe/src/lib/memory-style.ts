/**
 * The visual vocabulary of the belief graph, in one place.
 *
 * Status is the primary signal, so it owns colour — in the 3D scene and in the
 * DOM alike. Keeping both in this file is what stops a node reading amber in the
 * canvas and grey in the sidebar.
 *
 * The scale is deliberately not a rainbow: three quiet states and one loud one.
 * `contradicted` is the only status that should pull your eye, because it is the
 * only one that wants a decision from you.
 */

import type { MemoryCategory, MemoryStatus } from './types'

export const STATUS_COLOR: Record<MemoryStatus, string> = {
  active: '#34d399', // emerald — trusted, in retrieval
  contradicted: '#fbbf24', // amber — disputed, awaiting a human
  superseded: '#64748b', // slate — replaced, still queryable
  archived: '#3f3f46', // zinc — decayed out of retrieval, recoverable
}

export const STATUS_LABEL: Record<MemoryStatus, string> = {
  active: 'Active',
  contradicted: 'Disputed',
  superseded: 'Superseded',
  archived: 'Archived',
}

export const STATUS_BLURB: Record<MemoryStatus, string> = {
  active: 'Trusted and used in retrieval.',
  contradicted: 'In unresolved conflict. Still retrieved — the agent surfaces both sides.',
  superseded: 'Replaced by a newer belief. Never deleted; still queryable.',
  archived: 'Decayed below the confidence floor. Out of retrieval, recoverable.',
}

export const EDGE_COLOR = {
  supersedes: '#60a5fa', // blue — directed: new -> old
  conflicts_with: '#fbbf24', // amber — symmetric, and the one worth noticing
} as const

/** Tailwind classes for the status pill, matching STATUS_COLOR. */
export const STATUS_CLASS: Record<MemoryStatus, string> = {
  active: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300',
  contradicted: 'border-amber-500/40 bg-amber-500/15 text-amber-300',
  superseded: 'border-slate-500/30 bg-slate-500/10 text-slate-300',
  archived: 'border-zinc-600/40 bg-zinc-600/10 text-zinc-400',
}

export const CATEGORY_LABEL: Record<MemoryCategory, string> = {
  decision: 'Decision',
  preference: 'Preference',
  fact: 'Fact',
  event: 'Event',
  person: 'Person',
  constraint: 'Constraint',
}

/**
 * Node radius from confidence.
 *
 * `react-force-graph` treats `nodeVal` as sphere *volume*, so a linear value
 * makes a 0.9-confidence belief look barely larger than a 0.3 one. Cubing the
 * confidence makes the rendered radius track it linearly instead, which is the
 * comparison the eye actually makes.
 */
export function nodeVolume(confidence: number): number {
  const radius = 0.5 + 2.5 * clamp01(confidence)
  return radius ** 3
}

export function clamp01(value: number): number {
  return Math.max(0, Math.min(1, value))
}

/** Half-lives from the backend's CATEGORY_HALF_LIFE_DAYS, for the detail panel. */
export const CATEGORY_HALF_LIFE_DAYS: Record<MemoryCategory, number | null> = {
  constraint: 60,
  preference: 90,
  fact: 120,
  decision: 180,
  person: 365,
  event: null, // history is not a belief that can weaken
}
