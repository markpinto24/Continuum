import { useEffect, useRef, useState } from 'react'

import { useElementSize } from '@/hooks/use-element-size'
import { EDGE_COLOR, STATUS_COLOR } from '@/lib/memory-style'

/** One loop of the story, in seconds. */
const CYCLE = 13

/** What the caption says while each part of the story plays. */
const CAPTIONS = [
  { from: 0, text: 'A belief changes. The old one is kept and linked — never deleted.' },
  { from: 5.4, text: 'Two beliefs disagree. It asks you instead of guessing.' },
] as const

const STATIC_TIME = 9.5

type Rgb = [number, number, number]

/**
 * What Continuum does, told by a small belief graph on the sign-in page: a new
 * belief replaces an old one (an arrow, new → old, and the old one greys but
 * stays), then two beliefs disagree (amber, a line with no arrowhead — the
 * picture refuses to pick a side, as the resolver does) and it asks.
 *
 * Canvas 2D, one animation loop, nothing through React state but the caption.
 * With reduced motion it draws one still frame with both stories told. Without a
 * canvas (jsdom, a locked-down browser) it shows the captions alone.
 */
export function BeliefStory({ reduced }: { reduced: boolean }) {
  const [box, size] = useElementSize<HTMLDivElement>()
  const canvas = useRef<HTMLCanvasElement>(null)
  const [caption, setCaption] = useState(0)

  useEffect(() => {
    const el = canvas.current
    const { width, height } = size
    if (!el || width === 0 || height === 0) return
    const ctx = el.getContext('2d')
    if (!ctx) return

    const dpr = Math.min(window.devicePixelRatio || 1, 2)
    el.width = Math.round(width * dpr)
    el.height = Math.round(height * dpr)
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    const font = getComputedStyle(el).fontFamily || 'sans-serif'

    const draw = (t: number, clock: number) => {
      ctx.clearRect(0, 0, width, height)
      drawField(ctx, width, height, clock)
      drawStory(ctx, width, height, t, clock, font)
    }

    if (reduced) {
      draw(STATIC_TIME, 0)
      return
    }

    let frame = 0
    const start = performance.now()
    let shown = -1
    const tick = (now: number) => {
      const clock = (now - start) / 1000
      const t = clock % CYCLE
      draw(t, clock)
      const at = t >= CAPTIONS[1].from && t < CYCLE - 0.6 ? 1 : 0
      if (at !== shown) {
        shown = at
        setCaption(at)
      }
      frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [size, reduced])

  return (
    <figure className="flex flex-col gap-3">
      <div ref={box} className="relative h-[300px] w-full">
        <canvas ref={canvas} aria-hidden className="absolute inset-0 size-full" />
      </div>
      <figcaption
        className="min-h-10 text-sm leading-relaxed text-muted"
        aria-live="off"
      >
        {reduced ? (
          <>
            {CAPTIONS[0].text} {CAPTIONS[1].text}
          </>
        ) : (
          <span key={caption} className="block animate-fade-up">
            {CAPTIONS[caption].text}
          </span>
        )}
      </figcaption>
    </figure>
  )
}

// --- drawing -----------------------------------------------------------------

const ACTIVE = hex(STATUS_COLOR.active)
const SUPERSEDED = hex(STATUS_COLOR.superseded)
const DISPUTED = hex(STATUS_COLOR.contradicted)
const PENDING = hex(STATUS_COLOR.pending)
const ARROW = hex(EDGE_COLOR.supersedes)

/** The quiet graph the story happens in: memories drifting, lightly linked. */
const FIELD = Array.from({ length: 26 }, (_, i) => {
  const r = rand(i + 1)
  return {
    x: 0.04 + r() * 0.92,
    y: 0.06 + r() * 0.88,
    radius: 1.4 + r() * 2,
    phase: r() * Math.PI * 2,
    color: i % 9 === 0 ? PENDING : i % 4 === 0 ? SUPERSEDED : ACTIVE,
  }
})

function drawField(ctx: CanvasRenderingContext2D, w: number, h: number, clock: number) {
  const points = FIELD.map((n) => ({
    x: (n.x + Math.sin(clock * 0.18 + n.phase) * 0.012) * w,
    y: (n.y + Math.cos(clock * 0.15 + n.phase) * 0.016) * h,
    n,
  }))
  ctx.lineWidth = 1
  for (let i = 0; i < points.length; i++) {
    for (let j = i + 1; j < points.length; j++) {
      const d = Math.hypot(points[i].x - points[j].x, points[i].y - points[j].y)
      if (d > 120) continue
      ctx.strokeStyle = rgba([148, 163, 184], 0.09 * (1 - d / 120))
      ctx.beginPath()
      ctx.moveTo(points[i].x, points[i].y)
      ctx.lineTo(points[j].x, points[j].y)
      ctx.stroke()
    }
  }
  for (const { x, y, n } of points) {
    ctx.fillStyle = rgba(n.color, 0.35)
    ctx.beginPath()
    ctx.arc(x, y, n.radius, 0, Math.PI * 2)
    ctx.fill()
  }
}

function drawStory(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  t: number,
  clock: number,
  font: string,
) {
  // Everything the story adds fades out at the end of the loop, then begins again.
  const fade = 1 - ramp(t, CYCLE - 1.1, CYCLE - 0.2)
  const bob = (phase: number) => Math.sin(clock * 0.9 + phase) * 3

  // 1. A belief changes.
  const old = { x: 0.2 * w, y: 0.3 * h + bob(0) }
  const now = { x: 0.6 * w, y: 0.17 * h + bob(1.3) }
  const born = ease(ramp(t, 1.0, 1.8))
  const travel = ramp(t, 2.2, 3.2)
  const retired = ease(ramp(t, 3.1, 3.9))

  if (born > 0) {
    const reach = ease(ramp(t, 1.9, 2.4))
    drawArrow(ctx, now, old, rgba(ARROW, 0.55 * reach * fade), reach)
    if (travel > 0 && travel < 1) drawSpark(ctx, along(now, old, 0.12, ease(travel)), ARROW, fade)
  }
  drawNode(ctx, old, mix(ACTIVE, SUPERSEDED, retired), 9 - 3 * retired, fade, 0)
  drawLabel(ctx, old, 'Dave likes pizza', mix(ACTIVE, SUPERSEDED, retired), fade, font, retired > 0.5 ? 'superseded' : undefined)
  if (born > 0) {
    drawNode(ctx, now, ACTIVE, 9 * born, fade * born, 0)
    drawLabel(ctx, now, 'Dave prefers pasta now', ACTIVE, fade * born, font, retired > 0.5 ? 'holds' : undefined)
  }

  // 2. Two beliefs disagree: no arrow, both amber, and it asks.
  const a = { x: 0.3 * w, y: 0.72 * h + bob(2.1) }
  const b = { x: 0.74 * w, y: 0.6 * h + bob(3.4) }
  const second = ease(ramp(t, 5.4, 6.2))
  const third = ease(ramp(t, 6.4, 7.2))
  const clash = ease(ramp(t, 7.6, 8.4))
  const pulse = clash * (0.5 + 0.5 * Math.sin(clock * 3.2))

  if (second > 0) {
    if (clash > 0) {
      ctx.save()
      ctx.setLineDash([5, 6])
      ctx.lineDashOffset = -clock * 12
      ctx.strokeStyle = rgba(DISPUTED, 0.6 * clash * fade)
      ctx.lineWidth = 1.5
      ctx.beginPath()
      ctx.moveTo(a.x, a.y)
      ctx.lineTo(b.x, b.y)
      ctx.stroke()
      ctx.restore()
    }
    drawNode(ctx, a, mix(ACTIVE, DISPUTED, clash), 8.5, fade * second, pulse)
    drawLabel(ctx, a, 'Launch is on Friday', mix(ACTIVE, DISPUTED, clash), fade * second, font)
  }
  if (third > 0) {
    drawNode(ctx, b, mix(ACTIVE, DISPUTED, clash), 8.5 * third, fade * third, pulse)
    drawLabel(ctx, b, 'Launch is on Monday', mix(ACTIVE, DISPUTED, clash), fade * third, font)
  }
  if (clash > 0) {
    // Above the line: below it, the chip sat on the nodes' own labels.
    const mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 - 38 }
    drawChip(ctx, mid, 'Which holds? — asks you', DISPUTED, clash * fade, font)
  }
}

function drawNode(
  ctx: CanvasRenderingContext2D,
  p: { x: number; y: number },
  color: Rgb,
  radius: number,
  alpha: number,
  pulse: number,
) {
  if (alpha <= 0 || radius <= 0) return
  const halo = radius * (3.2 + pulse * 1.4)
  const glow = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, halo)
  glow.addColorStop(0, rgba(color, 0.42 * alpha))
  glow.addColorStop(1, rgba(color, 0))
  ctx.fillStyle = glow
  ctx.beginPath()
  ctx.arc(p.x, p.y, halo, 0, Math.PI * 2)
  ctx.fill()
  ctx.fillStyle = rgba(color, alpha)
  ctx.beginPath()
  ctx.arc(p.x, p.y, radius, 0, Math.PI * 2)
  ctx.fill()
  ctx.fillStyle = rgba([255, 255, 255], 0.35 * alpha)
  ctx.beginPath()
  ctx.arc(p.x - radius * 0.3, p.y - radius * 0.3, radius * 0.35, 0, Math.PI * 2)
  ctx.fill()
}

function drawLabel(
  ctx: CanvasRenderingContext2D,
  p: { x: number; y: number },
  text: string,
  color: Rgb,
  alpha: number,
  font: string,
  tag?: string,
) {
  if (alpha <= 0) return
  ctx.font = `500 12px ${font}`
  const textWidth = ctx.measureText(text).width
  ctx.font = `500 10px ${font}`
  const tagWidth = tag ? ctx.measureText(tag).width + 14 : 0
  const width = textWidth + 20 + tagWidth
  const x = Math.round(p.x - width / 2)
  const y = Math.round(p.y + 16)

  ctx.fillStyle = `rgba(8, 12, 20, ${0.78 * alpha})`
  roundRect(ctx, x, y, width, 24, 12)
  ctx.fill()
  ctx.strokeStyle = rgba(color, 0.35 * alpha)
  ctx.lineWidth = 1
  ctx.stroke()

  ctx.textBaseline = 'middle'
  ctx.font = `500 12px ${font}`
  ctx.fillStyle = `rgba(226, 232, 240, ${0.92 * alpha})`
  ctx.fillText(text, x + 10, y + 12.5)
  if (tag) {
    ctx.font = `500 10px ${font}`
    ctx.fillStyle = rgba(color, alpha)
    ctx.fillText(tag, x + 10 + textWidth + 10, y + 12.5)
  }
}

function drawChip(
  ctx: CanvasRenderingContext2D,
  p: { x: number; y: number },
  text: string,
  color: Rgb,
  alpha: number,
  font: string,
) {
  ctx.font = `600 11px ${font}`
  const width = ctx.measureText(text).width + 22
  const x = Math.round(p.x - width / 2)
  ctx.fillStyle = rgba(color, 0.16 * alpha)
  roundRect(ctx, x, p.y, width, 22, 11)
  ctx.fill()
  ctx.strokeStyle = rgba(color, 0.5 * alpha)
  ctx.stroke()
  ctx.textBaseline = 'middle'
  ctx.fillStyle = rgba(color, alpha)
  ctx.fillText(text, x + 11, p.y + 11.5)
}

/** A slightly curved edge from `from` to `to`, drawn as far as `reach`, arrowhead at the end. */
function drawArrow(
  ctx: CanvasRenderingContext2D,
  from: { x: number; y: number },
  to: { x: number; y: number },
  color: string,
  reach: number,
) {
  if (reach <= 0) return
  const start = along(from, to, 0.12, 0)
  const end = along(from, to, 0.12, reach)
  ctx.strokeStyle = color
  ctx.lineWidth = 1.5
  ctx.beginPath()
  ctx.moveTo(start.x, start.y)
  // Draw the curve in short steps so it can grow from its start.
  for (let s = 0.04; s <= reach + 1e-6; s += 0.04) {
    const q = along(from, to, 0.12, Math.min(s, reach))
    ctx.lineTo(q.x, q.y)
  }
  ctx.lineTo(end.x, end.y)
  ctx.stroke()
  if (reach < 0.98) return
  const before = along(from, to, 0.12, 0.94)
  const angle = Math.atan2(end.y - before.y, end.x - before.x)
  ctx.fillStyle = color
  ctx.beginPath()
  ctx.moveTo(end.x, end.y)
  ctx.lineTo(end.x - 9 * Math.cos(angle - 0.4), end.y - 9 * Math.sin(angle - 0.4))
  ctx.lineTo(end.x - 9 * Math.cos(angle + 0.4), end.y - 9 * Math.sin(angle + 0.4))
  ctx.closePath()
  ctx.fill()
}

function drawSpark(ctx: CanvasRenderingContext2D, p: { x: number; y: number }, color: Rgb, alpha: number) {
  const glow = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, 14)
  glow.addColorStop(0, rgba([255, 255, 255], 0.9 * alpha))
  glow.addColorStop(0.3, rgba(color, 0.7 * alpha))
  glow.addColorStop(1, rgba(color, 0))
  ctx.fillStyle = glow
  ctx.beginPath()
  ctx.arc(p.x, p.y, 14, 0, Math.PI * 2)
  ctx.fill()
}

// --- geometry and colour -----------------------------------------------------

/** The control point of the edge's gentle curve. */
function bend(from: { x: number; y: number }, to: { x: number; y: number }) {
  const mx = (from.x + to.x) / 2
  const my = (from.y + to.y) / 2
  const dx = to.x - from.x
  const dy = to.y - from.y
  return { x: mx - dy * 0.18, y: my + dx * 0.18 }
}

/** A point `s` of the way along the curve, leaving `inset` clear at each node. */
function along(from: { x: number; y: number }, to: { x: number; y: number }, inset: number, s: number) {
  const u = inset + (1 - 2 * inset) * s
  const c = bend(from, to)
  const a = (1 - u) * (1 - u)
  const b = 2 * (1 - u) * u
  const d = u * u
  return { x: a * from.x + b * c.x + d * to.x, y: a * from.y + b * c.y + d * to.y }
}

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  ctx.beginPath()
  ctx.moveTo(x + r, y)
  ctx.arcTo(x + w, y, x + w, y + h, r)
  ctx.arcTo(x + w, y + h, x, y + h, r)
  ctx.arcTo(x, y + h, x, y, r)
  ctx.arcTo(x, y, x + w, y, r)
  ctx.closePath()
}

function ramp(t: number, from: number, to: number) {
  return Math.min(1, Math.max(0, (t - from) / (to - from)))
}

function ease(x: number) {
  return x < 0.5 ? 4 * x * x * x : 1 - (-2 * x + 2) ** 3 / 2
}

function hex(value: string): Rgb {
  const n = parseInt(value.slice(1), 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}

function mix(a: Rgb, b: Rgb, t: number): Rgb {
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]
}

function rgba([r, g, b]: Rgb, alpha: number) {
  return `rgba(${Math.round(r)}, ${Math.round(g)}, ${Math.round(b)}, ${Math.max(0, Math.min(1, alpha))})`
}

/** A small seeded generator, so the field is the same on every visit. */
function rand(seed: number) {
  let s = seed * 9301 + 49297
  return () => {
    s = (s * 9301 + 49297) % 233280
    return s / 233280
  }
}
