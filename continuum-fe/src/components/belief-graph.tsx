import { useCallback, useEffect, useMemo, useRef } from 'react'
import ForceGraph3D, { type ForceGraphMethods } from 'react-force-graph-3d'
import {
  AdditiveBlending,
  CanvasTexture,
  FogExp2,
  Group,
  Mesh,
  MeshBasicMaterial,
  type Object3D,
  type PerspectiveCamera,
  type Scene,
  SphereGeometry,
  Sprite,
  SpriteMaterial,
  SRGBColorSpace,
  TorusGeometry,
  Vector3,
} from 'three'

import { useElementSize } from '@/hooks/use-element-size'
import {
  EDGE_COLOR,
  STATUS_COLOR,
  STATUS_LABEL,
  SUMMARY_COLOR,
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
 *   arrow   = supersedes, new -> old, with particles flowing the same way
 *   pulse   = conflicts_with, symmetric (no arrowhead); disputed nodes breathe
 *
 * Stability over spectacle: no bloom pass (it switches off the canvas's
 * antialiasing, and thin edges shimmered as the camera moved), no point
 * starfield (sub-pixel stars twinkled), no auto-rotation. The sky is painted
 * once into a texture, and labels are HTML laid out each frame so they never
 * overlap.
 */

/** `react-force-graph` mutates what you hand it, so these carry its extra fields. */
interface SceneNode extends GraphNode {
  x?: number
  y?: number
  z?: number
  __threeObj?: Object3D
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
  /** Pixels on the right covered by a floating panel: the view centres on the rest. */
  rightInset?: number
}

const SKY = '#050a10'
// Matches the library's default nodeRelSize, so decorations hug the sphere.
const NODE_REL_SIZE = 4
const LABEL_GAP = 6

export function BeliefGraph({ data, selectedId, onSelect, rightInset = 0 }: BeliefGraphProps) {
  const [containerRef, { width, height }] = useElementSize<HTMLDivElement>()
  const graphRef = useRef<ForceGraphMethods<SceneNode, SceneLink> | undefined>(undefined)
  const labelLayer = useRef<HTMLDivElement | null>(null)

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

  // Room to breathe: the default charge packs memories into a ball too dense to
  // click into, and short links hide which node an arrow points at.
  useEffect(() => {
    const graph = graphRef.current
    if (!graph) return
    graph.d3Force('charge')?.strength(-170)
    graph.d3Force('link')?.distance(64)
  }, [graphData])

  // The sky, fog and smooth controls. Set once per renderer.
  const ready = width > 0 && height > 0 && data.nodes.length > 0
  useEffect(() => {
    const graph = graphRef.current
    if (!ready || !graph) return
    const scene = graph.scene() as Scene
    scene.background = skyTexture()
    scene.fog = new FogExp2(SKY, 0.00038)
    const controls = graph.controls() as { enableDamping?: boolean; dampingFactor?: number }
    // Eased orbiting: the camera glides to a stop instead of jerking.
    controls.enableDamping = true
    controls.dampingFactor = 0.09
    return () => {
      scene.fog = null
    }
  }, [ready])

  // Centre the view on the part of the canvas the floating panel leaves open.
  useEffect(() => {
    const graph = graphRef.current
    if (!ready || !graph) return
    const camera = graph.camera() as PerspectiveCamera
    if (rightInset > 0) camera.setViewOffset(width, height, rightInset / 2, 0, width, height)
    else camera.clearViewOffset()
    camera.updateProjectionMatrix()
  }, [ready, width, height, rightInset])

  // Frame the graph once per dataset, when the layout first settles. Only once:
  // refitting on every settle would yank the camera back each time someone
  // orbits away to look at something.
  const framed = useRef(false)
  useEffect(() => {
    framed.current = false
  }, [graphData])
  const frameOnce = useCallback(() => {
    if (framed.current) return
    framed.current = true
    graphRef.current?.zoomToFit(800, 70 + rightInset / 4)
  }, [rightInset])

  // A selection frames itself *and its neighbours*: the point of selecting is
  // to see what a belief is connected to, not to fly into one glowing sphere.
  // The camera keeps its current angle and moves to a distance set by how far
  // apart the group is — close for a pair, further for a crowd.
  useEffect(() => {
    const graph = graphRef.current
    if (!graph || !neighbourhood) return
    const group = graphData.nodes.filter(
      (n) => neighbourhood.has(n.id) && n.x !== undefined && n.y !== undefined && n.z !== undefined,
    )
    if (group.length === 0) return
    const centre = new Vector3()
    for (const n of group) centre.add(new Vector3(n.x, n.y, n.z))
    centre.divideScalar(group.length)
    let spread = 0
    for (const n of group) spread = Math.max(spread, centre.distanceTo(new Vector3(n.x, n.y, n.z)))
    const camera = graph.camera() as PerspectiveCamera
    const direction = camera.position.clone().sub(centre).normalize()
    if (direction.lengthSq() === 0) direction.set(0, 0, 1)
    const distance = Math.max(150, spread * 4 + 90)
    const to = centre.clone().add(direction.multiplyScalar(distance))
    graph.cameraPosition({ x: to.x, y: to.y, z: to.z }, { x: centre.x, y: centre.y, z: centre.z }, 900)
  }, [graphData, neighbourhood])

  // --- Labels: HTML, laid out every frame, never overlapping -----------------

  const labelled = useRef<{ node: SceneNode; el: HTMLElement }[]>([])
  useEffect(() => {
    const layer = labelLayer.current
    if (!layer) return
    const els = new Map<string, HTMLElement>()
    for (const el of Array.from(layer.children) as HTMLElement[]) {
      if (el.dataset.id) els.set(el.dataset.id, el)
    }
    labelled.current = graphData.nodes
      .filter((node) => els.has(node.id))
      .map((node) => ({ node, el: els.get(node.id) as HTMLElement }))
  }, [graphData])

  useEffect(() => {
    if (!ready) return
    let frame = 0
    const point = new Vector3()
    const sizes = new WeakMap<HTMLElement, { w: number; h: number }>()
    const shown = new WeakMap<HTMLElement, string>()

    const hide = (el: HTMLElement) => {
      if (el.style.opacity !== '0') el.style.opacity = '0'
    }

    const draw = (time: number) => {
      frame = requestAnimationFrame(draw)
      const graph = graphRef.current
      if (!graph) return
      const camera = graph.camera() as PerspectiveCamera
      const focal = height / (2 * Math.tan((camera.fov * Math.PI) / 360))
      const visibleRight = width - rightInset - 12

      const candidates: {
        el: HTMLElement
        x: number
        y: number
        priority: number
        strong: boolean
      }[] = []
      for (const { node, el } of labelled.current) {
        // Disputed nodes breathe, gently — the one thing that wants a decision.
        if (node.status === 'contradicted' && !(neighbourhood && !neighbourhood.has(node.id))) {
          const glow = node.__threeObj?.getObjectByName('glow') as Sprite | undefined
          if (glow) glow.material.opacity = 0.45 + 0.3 * Math.sin(time / 520)
        }
        if (node.x === undefined || node.y === undefined || node.z === undefined) {
          hide(el)
          continue
        }
        const strong = neighbourhood?.has(node.id) ?? false
        if (neighbourhood && !strong) {
          hide(el)
          continue
        }
        point.set(node.x, node.y, node.z)
        const depth = point.distanceTo(camera.position)
        point.project(camera)
        if (point.z < -1 || point.z > 1) {
          hide(el)
          continue
        }
        const sx = ((point.x + 1) / 2) * width
        const sy = ((1 - point.y) / 2) * height
        const radius = Math.cbrt(nodeVolume(node.confidence)) * NODE_REL_SIZE
        const offset = Math.max(8, (radius * focal) / Math.max(depth, 1)) + LABEL_GAP
        const priority =
          (node.id === selectedId ? 1e6 : 0) +
          (strong ? 1e5 : 0) +
          (node.status === 'contradicted' ? 1e3 : 0) +
          node.confidence * 100 -
          depth * 0.05
        candidates.push({ el, x: sx, y: sy - offset, priority, strong })
      }

      // Most important first; anything that would overlap a placed label waits.
      candidates.sort((a, b) => b.priority - a.priority)
      const placed: { l: number; t: number; r: number; b: number }[] = []
      for (const c of candidates) {
        const mode = c.strong ? 'strong' : 'quiet'
        if (c.el.dataset.mode !== mode) {
          c.el.dataset.mode = mode
          sizes.delete(c.el)
        }
        let size = sizes.get(c.el)
        if (!size) {
          size = { w: c.el.offsetWidth, h: c.el.offsetHeight }
          sizes.set(c.el, size)
        }
        const l = c.x - size.w / 2
        const t = c.y - size.h
        const box = { l: l - 4, t: t - 2, r: l + size.w + 4, b: t + size.h + 2 }
        const clash =
          box.r > visibleRight ||
          box.l < 4 ||
          box.t < 4 ||
          placed.some((p) => box.l < p.r && box.r > p.l && box.t < p.b && box.b > p.t)
        if (clash) {
          hide(c.el)
          continue
        }
        placed.push(box)
        const transform = `translate3d(${Math.round(l)}px, ${Math.round(t)}px, 0)`
        if (shown.get(c.el) !== transform) {
          c.el.style.transform = transform
          shown.set(c.el, transform)
        }
        if (c.el.style.opacity !== '1') c.el.style.opacity = '1'
      }
    }

    frame = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(frame)
  }, [ready, width, height, rightInset, neighbourhood, selectedId])

  const nodeObject = useCallback(
    (node: SceneNode) =>
      nodeDecoration(node, { muted: isMuted(node.id), selected: node.id === selectedId }),
    [isMuted, selectedId],
  )

  const linkColor = useCallback(
    (link: SceneLink) => {
      const base = EDGE_COLOR[link.kind]
      return isMuted(endpointId(link.source)) && isMuted(endpointId(link.target))
        ? withAlpha(base, 0.08)
        : withAlpha(base, link.kind === 'conflicts_with' ? 0.95 : 0.85)
    },
    [isMuted],
  )

  return (
    <div
      ref={containerRef}
      className="relative h-full w-full overflow-hidden"
      // Shown for the instant before WebGL paints, so there is no flash.
      style={{ background: `radial-gradient(ellipse at 45% 40%, #0b1a26, ${SKY} 70%)` }}
    >
      {ready && (
        <ForceGraph3D<SceneNode, SceneLink>
          ref={graphRef}
          width={width}
          height={height}
          graphData={graphData}
          backgroundColor={SKY}
          showNavInfo={false}
          // Orbit, not the default trackball: it keeps "up" up, and damps.
          // Missing from the library's types, present at runtime.
          {...({ controlType: 'orbit' } as object)}
          nodeId="id"
          nodeLabel={nodeTooltip}
          nodeVal={(node) => nodeVolume(node.confidence)}
          nodeColor={(node) =>
            isMuted(node.id) ? withAlpha(STATUS_COLOR[node.status], 0.14) : STATUS_COLOR[node.status]
          }
          nodeOpacity={0.96}
          nodeResolution={24}
          nodeThreeObjectExtend
          nodeThreeObject={nodeObject}
          linkColor={linkColor}
          linkWidth={(link) => (link.kind === 'conflicts_with' ? 1.5 : 1)}
          linkOpacity={0.9}
          // A little curve, so two relations between the same pair can never
          // sit on top of each other and read as one.
          linkCurvature={0.12}
          // Arrows only on supersedes: it is the directed edge. A conflict is
          // symmetric — neither side won — so drawing it with an arrowhead would
          // assert exactly the thing nobody has decided yet.
          linkDirectionalArrowLength={(link) => (link.kind === 'supersedes' ? 4.5 : 0)}
          linkDirectionalArrowRelPos={0.95}
          linkDirectionalParticles={(link) => (link.kind === 'conflicts_with' ? 3 : 2)}
          linkDirectionalParticleWidth={(link) => (link.kind === 'conflicts_with' ? 2.2 : 1.6)}
          linkDirectionalParticleSpeed={(link) => (link.kind === 'conflicts_with' ? 0.007 : 0.004)}
          linkDirectionalParticleColor={(link) => EDGE_COLOR[link.kind]}
          onNodeClick={(node) => onSelect(node.id)}
          onBackgroundClick={() => onSelect(null)}
          cooldownTicks={140}
          warmupTicks={40}
          onEngineStop={frameOnce}
          enableNodeDrag={false}
        />
      )}

      {/* Labels sit above the canvas but never take a click from it. */}
      <div ref={labelLayer} aria-hidden className="pointer-events-none absolute inset-0">
        {data.nodes.map((node) => (
          <span key={node.id} data-id={node.id} className="graph-label absolute top-0 left-0">
            {node.label.length > 40 ? `${node.label.slice(0, 39)}…` : node.label}
          </span>
        ))}
      </div>
    </div>
  )
}

function nodeDecoration(
  node: SceneNode,
  { muted, selected }: { muted: boolean; selected: boolean },
): Object3D {
  const group = new Group()
  const radius = Math.cbrt(nodeVolume(node.confidence)) * NODE_REL_SIZE
  const color = STATUS_COLOR[node.status]

  // Soft light around the node, in its status colour — the glow, without a
  // bloom pass that would cost the whole scene its antialiasing.
  const glow = new Sprite(
    new SpriteMaterial({
      map: glowTexture(),
      color,
      transparent: true,
      opacity: muted ? 0.06 : selected ? 0.75 : 0.5,
      blending: AdditiveBlending,
      depthWrite: false,
    }),
  )
  glow.name = 'glow'
  glow.scale.setScalar(radius * (selected ? 4.6 : 4))
  group.add(glow)

  if (selected) {
    group.add(
      new Mesh(
        new TorusGeometry(radius * 1.55, Math.max(0.25, radius * 0.06), 8, 64),
        new MeshBasicMaterial({ color: '#e6faff', transparent: true, opacity: 0.85 }),
      ),
    )
  }
  if (node.shared) {
    group.add(
      new Mesh(
        new SphereGeometry(radius * 1.45, 14, 10),
        new MeshBasicMaterial({
          color: '#7dd3fc',
          wireframe: true,
          transparent: true,
          opacity: muted ? 0.05 : 0.28,
        }),
      ),
    )
  }
  if (node.kind === 'summary') {
    group.add(
      new Mesh(
        new TorusGeometry(radius * 1.8, radius * 0.1, 6, 48),
        new MeshBasicMaterial({
          color: SUMMARY_COLOR,
          transparent: true,
          opacity: muted ? 0.12 : 0.7,
        }),
      ),
    )
  }
  return group
}

// --- Textures, built once ------------------------------------------------------

let glowCache: CanvasTexture | null = null
function glowTexture(): CanvasTexture {
  if (glowCache) return glowCache
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = 128
  const ctx = canvas.getContext('2d')
  if (ctx) {
    const gradient = ctx.createRadialGradient(64, 64, 0, 64, 64, 64)
    gradient.addColorStop(0, 'rgba(255,255,255,0.85)')
    gradient.addColorStop(0.3, 'rgba(255,255,255,0.3)')
    gradient.addColorStop(1, 'rgba(255,255,255,0)')
    ctx.fillStyle = gradient
    ctx.fillRect(0, 0, 128, 128)
  }
  glowCache = new CanvasTexture(canvas)
  return glowCache
}

/**
 * The sky, painted once: a lit centre, faint nebula clouds and fixed stars.
 * Painted rather than rendered as points, so nothing in it can twinkle.
 */
function skyTexture(): CanvasTexture {
  const size = 1024
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = size
  const ctx = canvas.getContext('2d')
  if (ctx) {
    const base = ctx.createRadialGradient(size * 0.48, size * 0.42, 10, size / 2, size / 2, size * 0.75)
    base.addColorStop(0, '#0e2130')
    base.addColorStop(0.45, '#08131c')
    base.addColorStop(1, SKY)
    ctx.fillStyle = base
    ctx.fillRect(0, 0, size, size)

    const nebula = (x: number, y: number, r: number, color: string) => {
      const g = ctx.createRadialGradient(x, y, 0, x, y, r)
      g.addColorStop(0, color)
      g.addColorStop(1, 'rgba(0,0,0,0)')
      ctx.fillStyle = g
      ctx.fillRect(0, 0, size, size)
    }
    nebula(size * 0.22, size * 0.3, size * 0.32, 'rgba(56,189,248,0.07)')
    nebula(size * 0.8, size * 0.72, size * 0.36, 'rgba(139,92,246,0.06)')
    nebula(size * 0.62, size * 0.18, size * 0.22, 'rgba(45,212,191,0.05)')

    // Deterministic stars, so the sky is the same on every load.
    let seed = 7
    const random = () => {
      seed = (seed * 16807) % 2147483647
      return seed / 2147483647
    }
    for (let i = 0; i < 260; i += 1) {
      const x = random() * size
      const y = random() * size
      const r = random() < 0.92 ? 0.6 + random() * 0.5 : 1.1 + random() * 0.6
      ctx.fillStyle = `rgba(210,232,255,${0.18 + random() * 0.45})`
      ctx.beginPath()
      ctx.arc(x, y, r, 0, Math.PI * 2)
      ctx.fill()
    }
  }
  const texture = new CanvasTexture(canvas)
  texture.colorSpace = SRGBColorSpace
  return texture
}

function nodeTooltip(node: SceneNode): string {
  const subject = node.subject ? ` · ${escapeHtml(node.subject)}` : ''
  const shared = node.shared
    ? `<div style="color:#7dd3fc;font-size:11px;margin-top:.2rem">Shared with the team${
        node.shared_by_email ? ` by ${escapeHtml(node.shared_by_email)}` : ''
      }</div>`
    : ''
  const summary =
    node.kind === 'summary'
      ? `<div style="color:${SUMMARY_COLOR};font-size:11px;margin-top:.2rem">Summary of a subject — derived, not evidence</div>`
      : ''
  return `
    <div style="max-width:20rem;padding:.55rem .7rem;border-radius:.6rem;
                background:rgba(10,18,26,.92);border:1px solid #1f2d3a;color:#e6edf3;
                font-family:'Inter Variable',system-ui,sans-serif;font-size:12px;line-height:1.45;
                box-shadow:0 10px 30px rgba(0,0,0,.45)">
      <div style="margin-bottom:.35rem">${escapeHtml(node.label)}</div>
      <div style="color:#8b9aa8;font-size:11px">
        ${STATUS_LABEL[node.status]} · ${node.category}${subject} ·
        confidence ${node.confidence.toFixed(2)}
      </div>${shared}${summary}
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
