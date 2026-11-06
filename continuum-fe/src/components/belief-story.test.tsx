import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { BeliefStory } from '@/components/belief-story'

describe('BeliefStory', () => {
  it('tells the first part of the story while it plays', () => {
    render(<BeliefStory reduced={false} />)
    expect(screen.getByText(/A belief changes/)).toBeTruthy()
  })

  it('tells both parts at once when motion is reduced, since the loop never runs', () => {
    render(<BeliefStory reduced />)
    const caption = screen.getByText(/A belief changes/)
    expect(caption.textContent).toMatch(/It asks you instead of guessing/)
  })

  it('draws nothing, and does not throw, without a canvas', () => {
    // jsdom's getContext returns null (test/setup.ts), as a browser without one does.
    const { container } = render(<BeliefStory reduced={false} />)
    expect(container.querySelector('canvas')?.getAttribute('aria-hidden')).toBe('true')
  })
})
