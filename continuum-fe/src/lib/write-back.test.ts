import { describe, expect, it } from 'vitest'

import type { IngestResponse, Memory } from './types'
import { describeWriteBack } from './write-back'

function response(overrides: Partial<IngestResponse>): IngestResponse {
  return {
    source_id: 'chat:x',
    extracted: 0,
    created: [],
    duplicates_skipped: 0,
    reinforced: [],
    superseded: [],
    conflicts_raised: [],
    resolutions: [],
    ...overrides,
  }
}

const memory = { id: 'm' } as Memory

describe('describeWriteBack', () => {
  it('says so when nothing durable was found', () => {
    // The case that used to be silent — indistinguishable from memory being off.
    expect(describeWriteBack(response({ extracted: 0 }))).toMatch(/nothing stored/i)
  })

  it('does not call a confirmation a new memory', () => {
    // Used to read "Remembered 1" for a fact that only reinforced an existing one.
    const line = describeWriteBack(response({ extracted: 1, reinforced: ['a'] }))
    expect(line).toBe('Confirmed 1')
  })

  it('reports each outcome separately', () => {
    const line = describeWriteBack(
      response({
        extracted: 3,
        created: [memory, memory],
        superseded: ['old'],
        conflicts_raised: ['c'],
      }),
    )
    expect(line).toBe('Stored 2 new memories · superseded 1 · raised 1 dispute')
  })

  it('says nothing when there was no write-back at all', () => {
    expect(describeWriteBack(null)).toBeNull()
  })
})
