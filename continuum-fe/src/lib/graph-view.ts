/**
 * What the graph shows, derived from what the API sent: filtered, and — past a
 * few hundred nodes — grouped by subject. Pure, so the two rules that matter
 * are testable without a WebGL canvas:
 *
 * - **Half a dispute is never shown as settled.** A filter that hides one side
 *   of an open disagreement while the other stays visible brings it back — the
 *   graph's version of the chat never dropping a disputed counterpart.
 * - **A cluster is disputed if anything in it is.** Grouping must not bury the
 *   one status that wants a decision.
 */

import type {
  GraphEdge,
  GraphNode,
  GraphResponse,
  MemoryCategory,
  MemoryStatus,
} from './types'

/** A graph node as drawn: a memory, or a subject's memories folded into one. */
export interface ViewNode extends GraphNode {
  /** How many memories this node stands for: 1 for a memory, more for a cluster. */
  members: number
  /** Kept only because it is the other side of a visible dispute. */
  pulledIn?: boolean
}

export interface ViewGraph {
  nodes: ViewNode[]
  edges: GraphEdge[]
}

export interface GraphFilter {
  /** Statuses to hide (from the legend's toggles). */
  hiddenStatuses: ReadonlySet<MemoryStatus>
  /** Show only these categories; empty means all. */
  categories: ReadonlySet<MemoryCategory>
  /** Only memories recorded in the last this many days; null means all time. */
  sinceDays: number | null
}

export const NO_FILTER: GraphFilter = {
  hiddenStatuses: new Set(),
  categories: new Set(),
  sinceDays: null,
}

/** Above this many visible nodes, grouping by subject is on unless turned off. */
export const AUTO_CLUSTER_ABOVE = 300

export const CLUSTER_PREFIX = 'cluster:'
const NO_SUBJECT = '(no subject)'

export function isCluster(id: string): boolean {
  return id.startsWith(CLUSTER_PREFIX)
}

export function filterGraph(
  data: GraphResponse,
  filter: GraphFilter,
  now: Date = new Date(),
): ViewGraph & { pulledIn: number } {
  const cutoff =
    filter.sinceDays === null ? null : now.getTime() - filter.sinceDays * 86_400_000
  const passes = (node: GraphNode) =>
    !filter.hiddenStatuses.has(node.status) &&
    (filter.categories.size === 0 || filter.categories.has(node.category)) &&
    (cutoff === null || new Date(node.created_at).getTime() >= cutoff)

  const byId = new Map(data.nodes.map((node) => [node.id, node]))
  const shown = new Map<string, ViewNode>()
  for (const node of data.nodes) {
    if (passes(node)) shown.set(node.id, { ...node, members: 1 })
  }
  // The other side of every visible dispute, even if the filter would hide it.
  let pulledIn = 0
  for (const edge of data.edges) {
    if (edge.kind !== 'conflicts_with') continue
    for (const [here, there] of [
      [edge.source, edge.target],
      [edge.target, edge.source],
    ]) {
      const other = byId.get(there)
      if (shown.has(here) && other && !shown.has(there)) {
        shown.set(there, { ...other, members: 1, pulledIn: true })
        pulledIn += 1
      }
    }
  }
  return {
    nodes: [...shown.values()],
    edges: data.edges.filter((e) => shown.has(e.source) && shown.has(e.target)),
    pulledIn,
  }
}

/**
 * One node per subject, except the subjects in `expanded`, whose memories are
 * shown one by one. Edges are carried between whatever now stands for each end;
 * an edge inside one cluster is folded away with it.
 */
export function clusterBySubject(graph: ViewGraph, expanded: ReadonlySet<string>): ViewGraph {
  const groups = new Map<string, ViewNode[]>()
  for (const node of graph.nodes) {
    const subject = node.subject ?? NO_SUBJECT
    groups.set(subject, [...(groups.get(subject) ?? []), node])
  }

  const standsFor = new Map<string, string>()
  const nodes: ViewNode[] = []
  for (const [subject, members] of groups) {
    if (members.length === 1 || expanded.has(subject)) {
      for (const node of members) {
        standsFor.set(node.id, node.id)
        nodes.push(node)
      }
      continue
    }
    const id = `${CLUSTER_PREFIX}${subject}`
    for (const node of members) standsFor.set(node.id, id)
    nodes.push(clusterNode(id, subject, members))
  }

  const seen = new Set<string>()
  const edges: GraphEdge[] = []
  for (const edge of graph.edges) {
    const source = standsFor.get(edge.source)
    const target = standsFor.get(edge.target)
    if (!source || !target || source === target) continue
    const key = `${source}|${target}|${edge.kind}`
    if (seen.has(key)) continue
    seen.add(key)
    edges.push({ source, target, kind: edge.kind })
  }
  return { nodes, edges }
}

function clusterNode(id: string, subject: string, members: ViewNode[]): ViewNode {
  const disputed = members.some((m) => m.status === 'contradicted')
  const newest = members.reduce((a, b) => (a.created_at > b.created_at ? a : b))
  return {
    id,
    label: `${subject} · ${members.length} memories`,
    category: newest.category,
    // Disputed if anything in it is; otherwise the status most of it holds.
    status: disputed ? 'contradicted' : mostCommon(members.map((m) => m.status)),
    confidence: members.reduce((sum, m) => sum + m.confidence, 0) / members.length,
    subject: subject === NO_SUBJECT ? null : subject,
    created_at: newest.created_at,
    shared: members.every((m) => m.shared),
    shared_by_email: null,
    kind: 'fact',
    members: members.length,
  }
}

function mostCommon<T>(values: T[]): T {
  const counts = new Map<T, number>()
  for (const v of values) counts.set(v, (counts.get(v) ?? 0) + 1)
  return [...counts.entries()].reduce((a, b) => (b[1] > a[1] ? b : a))[0]
}
