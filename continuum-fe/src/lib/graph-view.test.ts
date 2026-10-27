import { describe, expect, it } from 'vitest'

import {
  CLUSTER_PREFIX,
  NO_FILTER,
  clusterBySubject,
  filterGraph,
  type GraphFilter,
} from './graph-view'
import type { GraphEdge, GraphNode, GraphResponse } from './types'

function node(id: string, change: Partial<GraphNode> = {}): GraphNode {
  return {
    id, label: id, category: 'decision', status: 'active', confidence: 0.7, subject: 'atlas',
    created_at: '2026-10-20T10:00:00Z', shared: false, shared_by_email: null, kind: 'fact',
    ...change,
  }
}

const NOW = new Date('2026-10-27T12:00:00Z')

function graph(nodes: GraphNode[], edges: GraphEdge[] = []): GraphResponse {
  return { nodes, edges } as GraphResponse
}

const filter = (change: Partial<GraphFilter>): GraphFilter => ({ ...NO_FILTER, ...change })

describe('filtering the graph', () => {
  it('hides what the filter excludes, and the edges to it', () => {
    const data = graph(
      [node('a'), node('b', { status: 'superseded' }), node('c', { category: 'preference' })],
      [{ source: 'a', target: 'b', kind: 'supersedes' }],
    )
    const view = filterGraph(data, filter({ hiddenStatuses: new Set(['superseded']) }), NOW)
    expect(view.nodes.map((n) => n.id)).toEqual(['a', 'c'])
    expect(view.edges).toEqual([])
    const decisions = filterGraph(data, filter({ categories: new Set(['decision']) }), NOW)
    expect(decisions.nodes.map((n) => n.id)).toEqual(['a', 'b'])
  })

  it('keeps only what was recorded within the window', () => {
    const data = graph([node('old', { created_at: '2026-09-01T00:00:00Z' }), node('new')])
    expect(filterGraph(data, filter({ sinceDays: 30 }), NOW).nodes.map((n) => n.id)).toEqual([
      'new',
    ])
  })

  it('never shows half a dispute: the hidden side comes back', () => {
    const data = graph(
      [
        node('postgres', { status: 'contradicted', category: 'decision' }),
        node('mongo', { status: 'contradicted', category: 'fact' }),
      ],
      [{ source: 'postgres', target: 'mongo', kind: 'conflicts_with' }],
    )
    const view = filterGraph(data, filter({ categories: new Set(['decision']) }), NOW)
    expect(view.nodes.map((n) => n.id).sort()).toEqual(['mongo', 'postgres'])
    expect(view.nodes.find((n) => n.id === 'mongo')?.pulledIn).toBe(true)
    expect(view.pulledIn).toBe(1)
    expect(view.edges).toHaveLength(1)
  })
})

describe('grouping by subject', () => {
  const data = graph(
    [
      node('a1'),
      node('a2', { status: 'contradicted' }),
      node('a3'),
      node('b1', { subject: 'billing' }),
      node('b2', { subject: 'billing', status: 'superseded' }),
      node('b3', { subject: 'billing', status: 'superseded' }),
      node('lone', { subject: 'search' }),
    ],
    [
      { source: 'a2', target: 'b1', kind: 'conflicts_with' },
      { source: 'a1', target: 'a3', kind: 'supersedes' },
      { source: 'a3', target: 'b2', kind: 'supersedes' },
      { source: 'a2', target: 'b2', kind: 'conflicts_with' },
    ],
  )

  it('folds each subject into one node, sized by its members', () => {
    const view = clusterBySubject(filterGraph(data, NO_FILTER, NOW), new Set())
    const ids = view.nodes.map((n) => n.id).sort()
    // A subject of one stays a memory: a cluster of one hides nothing.
    expect(ids).toEqual([`${CLUSTER_PREFIX}atlas`, `${CLUSTER_PREFIX}billing`, 'lone'])
    expect(view.nodes.find((n) => n.id === `${CLUSTER_PREFIX}atlas`)?.members).toBe(3)
  })

  it('calls a cluster disputed if anything in it is', () => {
    const view = clusterBySubject(filterGraph(data, NO_FILTER, NOW), new Set())
    const status = (id: string) => view.nodes.find((n) => n.id === id)?.status
    expect(status(`${CLUSTER_PREFIX}atlas`)).toBe('contradicted')
    expect(status(`${CLUSTER_PREFIX}billing`)).toBe('superseded') // most of it
  })

  it('carries edges between clusters once, and folds away those inside one', () => {
    const view = clusterBySubject(filterGraph(data, NO_FILTER, NOW), new Set())
    expect(view.edges).toEqual([
      { source: `${CLUSTER_PREFIX}atlas`, target: `${CLUSTER_PREFIX}billing`, kind: 'conflicts_with' },
      { source: `${CLUSTER_PREFIX}atlas`, target: `${CLUSTER_PREFIX}billing`, kind: 'supersedes' },
    ])
  })

  it('shows an expanded subject memory by memory', () => {
    const view = clusterBySubject(filterGraph(data, NO_FILTER, NOW), new Set(['atlas']))
    expect(view.nodes.map((n) => n.id)).toContain('a2')
    expect(view.edges).toContainEqual({ source: 'a1', target: 'a3', kind: 'supersedes' })
    expect(view.edges).toContainEqual({
      source: 'a2', target: `${CLUSTER_PREFIX}billing`, kind: 'conflicts_with',
    })
  })
})
