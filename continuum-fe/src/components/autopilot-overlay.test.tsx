import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { LumenStage } from '@/components/autopilot-overlay'

const LONG =
  'Your week has three gym days. Monday is push: bench press, overhead press and dips. ' +
  'Wednesday is pull: rows, pull-ups and curls. Friday is legs: squats, lunges and calf raises. ' +
  'Sunday is the long run, five kilometres at an easy pace.'

function stage(props: { answerBeside: boolean; answer: string; chatOnScreen?: boolean }) {
  render(
    <LumenStage
      phase="working"
      stage="speaking"
      caption="What is my week?"
      levelRef={{ current: 0 }}
      active
      needsTap={false}
      layout="full"
      onTap={() => {}}
      onInterrupt={() => {}}
      onThankYou={() => {}}
      onClose={() => {}}
      {...props}
    />,
  )
}

describe("Lumen's long answers", () => {
  it('are shown in full beside Lumen when the chat is not on screen', () => {
    stage({ answerBeside: true, answer: LONG })
    expect(screen.getByRole('region', { name: "Lumen's answer" }).textContent).toContain('calf raises')
  })

  it('stay a subtitle when there is no room given for the card', () => {
    stage({ answerBeside: false, answer: LONG })
    expect(screen.queryByRole('region', { name: "Lumen's answer" })).toBeNull()
    expect(screen.getByText(/easy pace/)).toBeTruthy()
  })

  it('are not shown at all while the chat beside Lumen shows them', () => {
    stage({ answerBeside: false, answer: LONG, chatOnScreen: true })
    expect(screen.queryByRole('region', { name: "Lumen's answer" })).toBeNull()
    expect(screen.queryByText(/easy pace/)).toBeNull()
    // What it is doing, and what you asked, stay.
    expect(screen.getByText(/What is my week\?/)).toBeTruthy()
  })

  it('a short answer is a subtitle either way', () => {
    stage({ answerBeside: true, answer: 'Mondays, Wednesdays and Fridays.' })
    expect(screen.queryByRole('region', { name: "Lumen's answer" })).toBeNull()
  })
})
