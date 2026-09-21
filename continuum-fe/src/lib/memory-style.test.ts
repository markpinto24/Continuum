import { describe, expect, it } from 'vitest'

import { STATUS_COLOR, nodeVolume } from './memory-style'
import type { MemoryStatus } from './types'

describe('nodeVolume', () => {
  it('scales radius linearly with confidence, not volume', () => {
    // react-force-graph reads nodeVal as volume, so the cube root of the value
    // is what the eye actually compares. A 0.9 belief should read visibly
    // bigger than a 0.3 one.
    const radius = (confidence: number) => Math.cbrt(nodeVolume(confidence))
    expect(radius(0.9) / radius(0.3)).toBeGreaterThan(1.9)
  })

  it('never returns a zero-size node', () => {
    // A belief decayed to the floor must still be clickable.
    expect(nodeVolume(0)).toBeGreaterThan(0)
  })

  it('clamps out-of-range confidence', () => {
    expect(nodeVolume(-1)).toBe(nodeVolume(0))
    expect(nodeVolume(2)).toBe(nodeVolume(1))
  })
})

describe('STATUS_COLOR', () => {
  it('covers every lifecycle status', () => {
    const statuses: MemoryStatus[] = ['active', 'superseded', 'contradicted', 'archived']
    for (const status of statuses) {
      expect(STATUS_COLOR[status]).toMatch(/^#[0-9a-f]{6}$/i)
    }
  })

  it('gives contradicted its own colour, distinct from active', () => {
    // The disputed state is the only one that should pull the eye.
    expect(STATUS_COLOR.contradicted).not.toBe(STATUS_COLOR.active)
  })
})
