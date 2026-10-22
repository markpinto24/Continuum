import { PerspectiveCamera } from 'three'
import { describe, expect, it } from 'vitest'

import { centreOnOpenArea } from './view-offset'

describe('centring the graph on the area the panel leaves open', () => {
  it('shifts the view by half the panel, and puts it back when something cleared it', () => {
    const camera = new PerspectiveCamera(50, 1920 / 1000)
    centreOnOpenArea(camera, 1920, 1000, 720)
    expect(camera.view).toMatchObject({ enabled: true, offsetX: 360, fullWidth: 1920 })

    // What the graph library's first update does, after ours had run.
    camera.clearViewOffset()
    centreOnOpenArea(camera, 1920, 1000, 720)
    expect(camera.view?.enabled).toBe(true)
    expect(camera.view?.offsetX).toBe(360)
  })

  it('follows the panel, and clears the offset when there is no panel', () => {
    const camera = new PerspectiveCamera(50, 1920 / 1000)
    centreOnOpenArea(camera, 1920, 1000, 720)
    centreOnOpenArea(camera, 1920, 1000, 72) // collapsed to the rail
    expect(camera.view?.offsetX).toBe(36)
    centreOnOpenArea(camera, 1920, 1000, 0)
    expect(camera.view?.enabled).toBeFalsy()
  })

  it('does nothing when the view is already right', () => {
    const camera = new PerspectiveCamera(50, 1920 / 1000)
    centreOnOpenArea(camera, 1920, 1000, 720)
    const before = camera.projectionMatrix.clone()
    camera.projectionMatrix.elements[0] = 42 // would be overwritten by a recompute
    centreOnOpenArea(camera, 1920, 1000, 720)
    expect(camera.projectionMatrix.elements[0]).toBe(42)
    expect(before.elements[0]).not.toBe(42)
  })
})
