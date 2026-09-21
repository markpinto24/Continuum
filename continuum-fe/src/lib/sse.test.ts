import { describe, expect, it } from 'vitest'

import { createSSEParser } from './sse'

/**
 * The parser exists because network chunks and SSE frames have nothing to do
 * with each other. These tests are all about that seam — a frame arriving in
 * pieces must produce exactly one event, and never a truncated one.
 */
describe('createSSEParser', () => {
  it('parses a complete frame', () => {
    const push = createSSEParser()
    expect(push('event: delta\ndata: {"text":"hi"}\n\n')).toEqual([
      { event: 'delta', data: '{"text":"hi"}' },
    ])
  })

  it('emits several frames delivered in one chunk', () => {
    const push = createSSEParser()
    const frames = push('event: delta\ndata: a\n\nevent: delta\ndata: b\n\n')
    expect(frames.map((f) => f.data)).toEqual(['a', 'b'])
  })

  it('holds back a frame that is still arriving', () => {
    const push = createSSEParser()
    expect(push('event: delta\ndata: {"text":"par')).toEqual([])
    expect(push('tial"}\n\n')).toEqual([{ event: 'delta', data: '{"text":"partial"}' }])
  })

  it('survives a split in the middle of the frame separator', () => {
    const push = createSSEParser()
    expect(push('event: done\ndata: {}\n')).toEqual([])
    expect(push('\n')).toEqual([{ event: 'done', data: '{}' }])
  })

  it('strips exactly one space after the colon, per the spec', () => {
    const push = createSSEParser()
    // A JSON payload starting with a space must keep the rest of its whitespace.
    expect(push('data:  {"x":1}\n\n')).toEqual([{ event: 'message', data: ' {"x":1}' }])
  })

  it('joins multi-line data with newlines', () => {
    const push = createSSEParser()
    expect(push('event: note\ndata: one\ndata: two\n\n')).toEqual([
      { event: 'note', data: 'one\ntwo' },
    ])
  })

  it('ignores comments and keep-alive frames', () => {
    const push = createSSEParser()
    expect(push(': keep-alive\n\nevent: delta\ndata: x\n\n')).toEqual([
      { event: 'delta', data: 'x' },
    ])
  })

  it('handles CRLF line endings from a proxy', () => {
    const push = createSSEParser()
    expect(push('event: delta\r\ndata: x\r\n\r\n')).toEqual([{ event: 'delta', data: 'x' }])
  })

  it('defaults the event name when the server sends only data', () => {
    const push = createSSEParser()
    expect(push('data: bare\n\n')).toEqual([{ event: 'message', data: 'bare' }])
  })
})
