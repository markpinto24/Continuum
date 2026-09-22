import { useCallback, useEffect, useMemo, useRef } from 'react'
import ForceGraph3D, { type ForceGraphMethods } from 'react-force-graph-3d'

import { useElementSize } from '@/hooks/use-element-size'
import {
  EDGE_COLOR,
  STATUS_COLOR,
  STATUS_LABEL,
  nodeVolume,
} from '@/lib/memory-style'
import type { GraphEdgeKind, GraphNode, GraphResponse } from '@/lib/types'

/**
 * The belief graph, rendered in three dimensions.
 *
 * Why 3D and not a flat d3 graph: a belief graph is not a tree. A single subject
 * accumulates supersede chains *and* conflict edges that cross between chains,
 * and in two dimensions those cross-links become an unreadable hairball at about
 * thirty nodes. Depth buys the separation that makes "this belief replaced that
 * one, and both are disputed by a third" legible at a glance.
 *
 * The encoding, which is the whole point of the view:
 *   colour  = lifecycle status   (amber = disputed, and nothing else is loud)
 *   size    = confidence          (how much the system still trusts it)
 *   arrow   = supersedes, new -> old, so a belief's history reads in one direction
 *   pulse   = conflicts_with, symmetric and deliberately attention-seeking
 */

/** `react-force-graph` mutates what you hand it, so these carry its extra fields. */
interface SceneNode extends GraphNode {
  x?: number
  y?: number
  z?: number
}

interface SceneLink {
  source: string | SceneNode
  target: string | SceneNode
  kind: GraphEdgeKind
}

export interface BeliefGraphProps {
  data: GraphResponse
  selectedId: string | null
  onSelect: (id: string | null) => void
}

export function BeliefGraph({ data, selectedId, onSelect }: BeliefGraphProps) {
  const [containerRef, { width, height }] = useElementSize<HTMLDivElement>()
  const graphRef = useRef<ForceGraphMethods<SceneNode, SceneLink> | undefined>(undefined)

  /**
   * Clone before handing over. The library replaces each link's `source`/`target`
   * string with a live node reference and writes simulation coordinates onto the
   * nodes — mutating the response object in place. Without this copy, a refetch
   * would be diffed against data the renderer had already rewritten.
   */
  const graphData = useMemo(
    () => ({
      nodes: data.nodes.map((node): SceneNode => ({ ...node })),
      links: data.edges.map((edge): SceneLink => ({ ...edge })),
    }),
    [data],
  )

  /** Every node one hop from the selection, for dimming everything else. */
  const neighbourhood = useMemo(() => {
    if (!selectedId) return null
    const ids = new Set<string>([selectedId])
    for (const edge of data.edges) {
      if (edge.source === selectedId) ids.add(edge.target)
      if (edge.target === selectedId) ids.add(edge.source)
    }
    return ids
  }, [data.edges, selectedId])

  const isMuted = useCallback(
    (id: string) => neighbourhood !== null && !neighbourhood.has(id),
    [neighbourhood],
  )

  // Push the nodes apart a little. The default charge packs a few hundred
  // memories into a ball too dense to click into.
  useEffect(() => {
    const graph = graphRef.current
    if (!graph) return
    graph.d3Force('charge')?.strength(-140)
    graph.d3Force('link')?.distance(48)
  }, [graphData])

  // Frame the graph once per dataset, when the layout first settles. Without
  // this the camera stays where the library put it before the simulation ran:
  // a sparse graph with no edges repels itself out of frame, and nodes end up
  // under the legend. Only once per dataset — refitting on every settle would
  // yank the camera back each time someone orbits away to look at something.
  const framed = useRef(false)
  useEffect(() => {
    framed.current = false
  }, [graphData])

  const frameOnce = useCallback(() => {
    if (framed.current) return
    framed.current = true
    graphRef.current?.zoomToFit(600, 90)
  }, [])

  const linkColor = useCallback(
    (link: SceneLink) => {
      const base = EDGE_COLOR[link.kind]
      return isMuted(endpointId(link.source)) && isMuted(endpointId(link.target))
        ? withAlpha(base, 0.12)
        : withAlpha(base, link.kind === 'conflicts_with' ? 0.95 : 0.55)
    },
    [isMuted],
  )

  return (
    <div ref={containerRef} className="h-full w-full">
      {data.nodes.length === 0 ? (
        <div className="flex h-full items-center justify-center text-center text-sm text-muted">
          <div className="max-w-sm px-6">
            <p className="mb-1 font-medium text-foreground">No memories yet</p>
            <p className="leading-relaxed">
              Feed this user a note through <code className="text-accent">POST /ingest</code>,
              or just talk to them in the chat panel — every turn is extracted and lands here.
            </p>
          </div>
        </div>
      ) : (
        width > 0 &&
        height > 0 && (
        <ForceGraph3D<SceneNode, SceneLink>
          ref={graphRef}
          width={width}
          height={height}
          graphData={graphData}
          backgroundColor="rgba(0,0,0,0)"
          showNavInfo={false}
          nodeId="id"
          nodeLabel={nodeTooltip}
          nodeVal={(node) => nodeVolume(node.confidence)}
          nodeColor={(node) =>
            isMuted(node.id)
              ? withAlpha(STATUS_COLOR[node.status], 0.15)
              : STATUS_COLOR[node.status]
          }
          nodeOpacity={0.92}
          nodeResolution={16}
          linkColor={linkColor}
          linkWidth={(link) => (link.kind === 'conflicts_with' ? 1.4 : 0.6)}
          linkOpacity={0.55}
          // Arrows only on supersedes: it is the directed edge. A conflict is
          // symmetric — neither side won — so drawing it with an arrowhead would
          // assert exactly the thing nobody has decided yet.
          linkDirectionalArrowLength={(link) => (link.kind === 'supersedes' ? 3.5 : 0)}
          linkDirectionalArrowRelPos={0.92}
          linkDirectionalParticles={(link) => (link.kind === 'conflicts_with' ? 3 : 0)}
          linkDirectionalParticleWidth={1.6}
          linkDirectionalParticleSpeed={0.006}
          onNodeClick={(node) => onSelect(node.id)}
          onBackgroundClick={() => onSelect(null)}
          cooldownTicks={120}
          warmupTicks={30}
          onEngineStop={frameOnce}
          enableNodeDrag={false}
        />
        )
      )}
    </div>
  )
}

function nodeTooltip(node: SceneNode): string {
  const subject = node.subject ? ` · ${escapeHtml(node.subject)}` : ''
  return `
    <div style="max-width:20rem;padding:.5rem .625rem;border-radius:.5rem;
                background:#1c2128;border:1px solid #30363d;color:#e6edf3;
                font-size:12px;line-height:1.45">
      <div style="margin-bottom:.35rem">${escapeHtml(node.label)}</div>
      <div style="color:#8b949e;font-size:11px">
        ${STATUS_LABEL[node.status]} · ${node.category}${subject} ·
        confidence ${node.confidence.toFixed(2)}
      </div>
    </div>`
}

function endpointId(endpoint: string | SceneNode): string {
  return typeof endpoint === 'string' ? endpoint : endpoint.id
}

/** `#rrggbb` -> `rgba(...)`, so muted nodes fade instead of changing hue. */
function withAlpha(hex: string, alpha: number): string {
  const value = parseInt(hex.slice(1), 16)
  const r = (value >> 16) & 255
  const g = (value >> 8) & 255
  const b = value & 255
  return `rgba(${r},${g},${b},${alpha})`
}

function escapeHtml(text: string): string {
  return text.replace(
    /[&<>"']/g,
    (char) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char] ?? char,
  )
}
