import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ConflictPair, Memory } from '@/lib/types'

const api = vi.hoisted(() => ({
  conflicts: vi.fn(),
  resolveConflict: vi.fn(),
  resolveTeamConflict: vi.fn(),
  searchMemories: vi.fn(),
  memory: vi.fn(),
  reinforce: vi.fn(),
  rejectMemory: vi.fn(),
  forgetMemory: vi.fn(),
}))
vi.mock('@/lib/api', () => ({ api }))

import { useVoiceActions } from './use-voice-actions'

function memory(id: string, content: string, extra: Partial<Memory> = {}): Memory {
  return { id, user_id: 'mark', content, status: 'active', subject: null, redacted_at: null, ...extra } as Memory
}

const friday = memory('m1', 'Launch is on Friday')
const monday = memory('m2', 'Launch is on Monday')
const dispute: ConflictPair = { memory: friday, conflicting: [monday], team: false }

function lumen(cited: string[] = []) {
  const spoken: string[] = []
  const onGraphChanged = vi.fn()
  const { result } = renderHook(() =>
    useVoiceActions({
      say: async (text) => {
        spoken.push(text)
      },
      note: () => {},
      onBusy: () => {},
      onGraphChanged,
      lastCited: () => cited,
    }),
  )
  const hear = async (said: string) => {
    let handled = false
    await act(async () => {
      handled = await result.current.handle(said)
    })
    return handled
  }
  const last = () => spoken[spoken.length - 1]
  return { hear, last, spoken, onGraphChanged, result }
}

/** Nothing about any memory was changed. */
function nothingChanged() {
  for (const call of [api.resolveConflict, api.resolveTeamConflict, api.reinforce, api.rejectMemory, api.forgetMemory]) {
    expect(call).not.toHaveBeenCalled()
  }
}

beforeEach(() => {
  for (const fn of Object.values(api)) fn.mockReset()
  api.conflicts.mockResolvedValue({ total: 1, conflicts: [dispute] })
  api.resolveConflict.mockResolvedValue({})
  api.resolveTeamConflict.mockResolvedValue({})
  api.reinforce.mockResolvedValue({})
  api.rejectMemory.mockResolvedValue({ memory: friday, restored: [] })
  api.forgetMemory.mockResolvedValue({})
})

describe('settling a dispute aloud', () => {
  it('reads it, reads the decision back, and settles only on a yes — keeping the words', async () => {
    const lumenSays = lumen()
    expect(await lumenSays.hear("Let's go through the disagreements")).toBe(true)
    expect(lumenSays.last()).toMatch(/First: “Launch is on Friday”. Second: “Launch is on Monday”/)

    await lumenSays.hear('The second one.')
    expect(lumenSays.last()).toMatch(/“Launch is on Monday” holds.*Shall I settle it/)
    nothingChanged() // announced, not done

    api.conflicts.mockResolvedValue({ total: 0, conflicts: [] })
    await lumenSays.hear('Yes.')
    expect(api.resolveConflict).toHaveBeenCalledWith({
      winner_id: 'm2',
      loser_ids: ['m1'],
      keep_both: false,
      said: 'The second one. — Yes.',
      spoken: true,
    })
    expect(lumenSays.last()).toMatch(/Settled. That was the last one/)
    expect(lumenSays.onGraphChanged).toHaveBeenCalled()
  })

  it.each(['No.', 'Wait.', 'hmm', 'yes but the other one'])('changes nothing on "%s"', async (reply) => {
    const lumenSays = lumen()
    await lumenSays.hear('What are my disputes?')
    await lumenSays.hear('the first')
    await lumenSays.hear(reply)
    nothingChanged()
    expect(lumenSays.last()).toMatch(/haven’t changed anything/)
    // And the read-back is not still waiting: a later "yes" settles nothing.
    await lumenSays.hear('yes')
    nothingChanged()
  })

  it('asks once more when it did not catch which, then leaves it in the inbox', async () => {
    const lumenSays = lumen()
    await lumenSays.hear('What are my disputes?')
    await lumenSays.hear('the fist one')
    expect(lumenSays.last()).toMatch(/didn’t catch which/)
    await lumenSays.hear('the purse one')
    expect(lumenSays.last()).toMatch(/leave it in your inbox/)
    nothingChanged()
  })

  it('a long reply that is not an answer goes to the chat, and nothing is decided', async () => {
    const lumenSays = lumen()
    await lumenSays.hear('What are my disputes?')
    expect(await lumenSays.hear('Actually tell me what I decided about the database migration last week')).toBe(false)
    nothingChanged()
  })

  it('both are true keeps both', async () => {
    const lumenSays = lumen()
    await lumenSays.hear('What are my disputes?')
    await lumenSays.hear('both are true')
    await lumenSays.hear('yes')
    expect(api.resolveConflict).toHaveBeenCalledWith(expect.objectContaining({ keep_both: true, spoken: true }))
  })

  it("settles yours against the team's through the team route, with the words", async () => {
    const team = memory('t1', 'We deploy on Fridays', { user_id: '_shared' })
    api.conflicts.mockResolvedValue({ total: 1, conflicts: [{ memory: friday, conflicting: [team], team: true }] })
    const lumenSays = lumen()
    await lumenSays.hear('What are my disputes?')
    expect(lumenSays.last()).toMatch(/yours against the team's/)
    await lumenSays.hear("the team's")
    await lumenSays.hear('yes')
    expect(api.resolveTeamConflict).toHaveBeenCalledWith('m1', 't1', 'team_holds', { said: "the team's — yes" })
  })

  it('skips one and offers the next, never re-reading a skipped one', async () => {
    const other: ConflictPair = { memory: memory('m3', 'I live in Pune'), conflicting: [memory('m4', 'I live in Mumbai')], team: false }
    api.conflicts.mockResolvedValue({ total: 2, conflicts: [dispute, other] })
    const lumenSays = lumen()
    await lumenSays.hear('What are my disputes?')
    await lumenSays.hear('skip')
    expect(lumenSays.last()).toMatch(/Skipped.*I live in Pune/)
    nothingChanged()
  })

  it('says so when there is nothing to settle', async () => {
    api.conflicts.mockResolvedValue({ total: 0, conflicts: [] })
    const lumenSays = lumen()
    await lumenSays.hear('Do I have any disputes?')
    expect(lumenSays.last()).toMatch(/No disagreements are waiting/)
  })
})

describe('acting on one memory aloud', () => {
  const age = memory('a1', 'The user is 22 years old')

  it('forgets only on "yes, forget it" — a bare yes is asked again', async () => {
    api.searchMemories.mockResolvedValue({ query: '', results: [{ memory: age, score: 0.8 }] })
    const lumenSays = lumen()
    await lumenSays.hear('Forget that I am 22')
    expect(lumenSays.last()).toMatch(/erases “The user is 22 years old” for good.*yes, forget it/)
    await lumenSays.hear('Yes.')
    expect(lumenSays.last()).toMatch(/say: yes, forget it/)
    nothingChanged()
    await lumenSays.hear('Yes, forget it.')
    expect(api.forgetMemory).toHaveBeenCalledWith('a1')
    expect(lumenSays.last()).toMatch(/Forgotten/)
  })

  it('a no to forgetting leaves it', async () => {
    api.searchMemories.mockResolvedValue({ query: '', results: [{ memory: age, score: 0.8 }] })
    const lumenSays = lumen()
    await lumenSays.hear('Forget that I am 22')
    await lumenSays.hear('No, keep it')
    nothingChanged()
  })

  it('reads out close matches and asks which, never picking one itself', async () => {
    const pune = memory('p1', 'The user lives in Pune')
    const kothrud = memory('p2', 'The user lives in Kothrud, Pune')
    api.searchMemories.mockResolvedValue({
      query: '',
      results: [
        { memory: pune, score: 0.81 },
        { memory: kothrud, score: 0.79 },
        { memory: memory('p3', 'The user works in Mumbai'), score: 0.5 },
      ],
    })
    const lumenSays = lumen()
    await lumenSays.hear('I live in Pune is still true')
    expect(lumenSays.last()).toMatch(/I found 2.*First: “The user lives in Pune”.*Second: “The user lives in Kothrud, Pune”/)
    nothingChanged()
    await lumenSays.hear('the second')
    expect(lumenSays.last()).toMatch(/mark “The user lives in Kothrud, Pune” as still true/)
    await lumenSays.hear('yes')
    expect(api.reinforce).toHaveBeenCalledWith('p2')
  })

  it('finds nothing below the match floor, and says so', async () => {
    api.searchMemories.mockResolvedValue({ query: '', results: [{ memory: age, score: 0.2 }] })
    const lumenSays = lumen()
    await lumenSays.hear('Forget my favourite colour')
    expect(lumenSays.last()).toMatch(/couldn't find a memory like that/)
    nothingChanged()
  })

  it('"that" is the memory the last answer stood on', async () => {
    api.memory.mockResolvedValue(age)
    const lumenSays = lumen(['a1'])
    await lumenSays.hear("That isn't a real fact")
    expect(lumenSays.last()).toMatch(/set “The user is 22 years old” aside/)
    await lumenSays.hear('yes')
    expect(api.rejectMemory).toHaveBeenCalledWith('a1', 'not_a_fact', expect.stringContaining('yes'))
  })

  it("never offers the team's memory, or one already forgotten", async () => {
    api.searchMemories.mockResolvedValue({
      query: '',
      results: [
        { memory: memory('t', 'The user is 22 years old', { user_id: '_shared' }), score: 0.9 },
        { memory: memory('r', '', { redacted_at: '2026-11-01T00:00:00Z' }), score: 0.9 },
      ],
    })
    const lumenSays = lumen()
    await lumenSays.hear('Forget that I am 22')
    expect(lumenSays.last()).toMatch(/couldn't find/)
  })

  it('a failure says so, and nothing is left waiting for a yes', async () => {
    api.searchMemories.mockRejectedValue(new Error('Cannot reach the API'))
    const lumenSays = lumen()
    await lumenSays.hear('Forget that I am 22')
    expect(lumenSays.last()).toMatch(/Something went wrong: Cannot reach the API. I haven’t changed anything/)
    await lumenSays.hear('yes, forget it')
    nothingChanged()
  })

  it('stopping Lumen drops what it was waiting for', async () => {
    api.searchMemories.mockResolvedValue({ query: '', results: [{ memory: age, score: 0.8 }] })
    const lumenSays = lumen()
    await lumenSays.hear('Forget that I am 22')
    act(() => lumenSays.result.current.reset())
    expect(await lumenSays.hear('yes, forget it')).toBe(false)
    nothingChanged()
  })

  it('an ordinary question is left to the chat', async () => {
    const lumenSays = lumen()
    expect(await lumenSays.hear('What should I eat before the gym?')).toBe(false)
    nothingChanged()
  })
})
