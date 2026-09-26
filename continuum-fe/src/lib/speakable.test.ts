import { describe, expect, it } from 'vitest'

import { speechChunks, toSpeakableText } from './speakable'

describe('toSpeakableText', () => {
  it('drops the markup a listener would hear as noise', () => {
    const md = '## Status\n\n- **Mark**: Administrator [1]\n- `test1continuum`: New user [2][3]\n'
    expect(toSpeakableText(md)).toBe('Status Mark: Administrator test1continuum: New user')
  })

  it('announces code instead of reading it', () => {
    expect(toSpeakableText('Run this:\n```bash\nrm -rf /\n```\nDone.')).toBe(
      'Run this: Code block omitted. Done.',
    )
  })

  it('keeps link text and drops the URL', () => {
    expect(toSpeakableText('See [the docs](https://example.com/x).')).toBe('See the docs.')
  })
})

describe('speechChunks', () => {
  it('keeps sentences whole and chunks under the cap', () => {
    const text = Array.from({ length: 12 }, (_, i) => `This is sentence number ${i + 1}.`).join(' ')
    const chunks = speechChunks(text)
    expect(chunks.length).toBeGreaterThan(1)
    expect(chunks.every((c) => c.length <= 220)).toBe(true)
    expect(chunks.join(' ')).toBe(text)
  })

  it('splits a run-on sentence at word boundaries', () => {
    const chunks = speechChunks(Array.from({ length: 80 }, () => 'word').join(' '))
    expect(chunks.every((c) => c.length <= 220 && !c.startsWith(' '))).toBe(true)
  })

  it('handles text without a final full stop', () => {
    expect(speechChunks('Hello there. How are you')).toEqual(['Hello there. How are you'])
  })
})
