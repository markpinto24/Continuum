import type { PerspectiveCamera } from 'three'

/**
 * Shift the projection so the scene's centre sits in the middle of the area the
 * floating panel leaves open. A no-op when already right, so it is cheap to call
 * every frame.
 */
export function centreOnOpenArea(
  camera: PerspectiveCamera,
  width: number,
  height: number,
  rightInset: number,
): void {
  const view = camera.view
  if (rightInset > 0) {
    const offsetX = rightInset / 2
    if (
      view?.enabled &&
      view.offsetX === offsetX &&
      view.fullWidth === width &&
      view.fullHeight === height
    ) {
      return
    }
    camera.setViewOffset(width, height, offsetX, 0, width, height)
  } else {
    if (!view?.enabled) return
    camera.clearViewOffset()
  }
  camera.updateProjectionMatrix()
}
